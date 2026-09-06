import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { Button } from '../../components/Button'
import { EmptyState } from '../../components/EmptyState'
import { ErrorBox } from '../../components/ErrorBox'
import { PageSpinner, Spinner } from '../../components/Spinner'
import { useToast } from '../../components/Toast'
import { api } from '../../lib/api'
import { useAuth } from '../../lib/auth'
import { cx, formatDate, formatTime, initials, parseDate, relativeTime } from '../../lib/format'
import { useSocket } from '../../lib/ws'
import type {
  ChatSocketEvent,
  ChatSocketSend,
  Conversation,
  Message,
  MessageCreate,
  Page,
} from '../../lib/types'

/**
 * FR 2.2 -- in-app messaging.
 *
 * The socket is the fast path, never the only one:
 * history comes from `GET /conversations/{id}/messages`, a send falls back to
 * `POST` when the socket is not open, and every reconnect re-pulls the thread.
 */

const conversationKeys = {
  list: ['conversations'] as const,
  messages: (conversationId: string) => ['conversations', conversationId, 'messages'] as const,
}

type ConversationsResponse = Conversation[] | Page<Conversation>
type MessagesResponse = Message[] | Page<Message>

function listOf<T>(data: T[] | Page<T> | undefined): T[] {
  if (!data) return []
  return Array.isArray(data) ? data : data.items
}

/** Oldest first -- the order a thread reads in. */
function chronological(messages: Message[]): Message[] {
  return [...messages].sort(
    (a, b) => (parseDate(a.timestamp)?.getTime() ?? 0) - (parseDate(b.timestamp)?.getTime() ?? 0),
  )
}

// ---------------------------------------------------------------------------
// inbox
// ---------------------------------------------------------------------------

function ConversationRow({
  conversation,
  active,
}: {
  conversation: Conversation
  active: boolean
}) {
  const unread = conversation.unread_count > 0
  // `other_user_name` is the only name on the wire; the id is carried separately.
  const otherName = conversation.other_user_name ?? 'A student'

  return (
    <li>
      <Link
        to={`/messages/${conversation.conversation_id}`}
        aria-current={active ? 'true' : undefined}
        className={cx(
          'flex gap-3 px-4 py-3 transition-colors',
          active ? 'bg-brand-50' : 'hover:bg-slate-50',
        )}
      >
        <span
          aria-hidden="true"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-600"
        >
          {initials(conversation.other_user_name)}
        </span>

        <span className="min-w-0 flex-1">
          <span className="flex items-baseline justify-between gap-2">
            <span
              className={cx(
                'truncate text-sm',
                unread ? 'font-semibold text-slate-900' : 'font-medium text-slate-800',
              )}
            >
              {otherName}
            </span>
            <span className="shrink-0 text-xs text-slate-400">
              {conversation.last_message_at ? relativeTime(conversation.last_message_at) : ''}
            </span>
          </span>

          {conversation.listing_title ? (
            <span className="mt-0.5 block truncate text-xs font-medium text-brand-700">
              {conversation.listing_title}
            </span>
          ) : null}

          <span className="mt-0.5 flex items-center gap-2">
            <span
              className={cx(
                'min-w-0 flex-1 truncate text-xs',
                unread ? 'text-slate-700' : 'text-slate-500',
              )}
            >
              {conversation.last_message_preview ?? 'No messages yet'}
            </span>
            {unread ? (
              <span className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-brand-600 px-1.5 text-[10px] font-semibold leading-none text-white">
                {conversation.unread_count > 9 ? '9+' : conversation.unread_count}
              </span>
            ) : null}
          </span>
        </span>
      </Link>
    </li>
  )
}

// ---------------------------------------------------------------------------
// page
// ---------------------------------------------------------------------------

export default function MessagesPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const toast = useToast()

  const activeId = id ?? null
  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  const conversationsQuery = useQuery({
    queryKey: conversationKeys.list,
    queryFn: () => api.get<ConversationsResponse>('/conversations'),
  })

  const messagesQuery = useQuery({
    queryKey: conversationKeys.messages(activeId ?? 'none'),
    queryFn: () =>
      api.get<MessagesResponse>(`/conversations/${activeId ?? ''}/messages`, {
        query: { page: 1, page_size: 200 },
      }),
    enabled: Boolean(activeId),
  })

  const conversations = listOf(conversationsQuery.data)
  const active = conversations.find((item) => item.conversation_id === activeId) ?? null
  const messages = useMemo(() => chronological(listOf(messagesQuery.data)), [messagesQuery.data])

  /** Splice a socket-delivered message into the cached thread. */
  const appendMessage = useCallback(
    (message: Message): void => {
      queryClient.setQueryData<MessagesResponse>(
        conversationKeys.messages(message.conversation_id),
        (previous) => {
          if (!previous) return previous
          const existing = listOf(previous)
          if (existing.some((item) => item.message_id === message.message_id)) return previous
          return Array.isArray(previous)
            ? [...previous, message]
            : { ...previous, items: [...previous.items, message], total: previous.total + 1 }
        },
      )
    },
    [queryClient],
  )

  const socket = useSocket<ChatSocketEvent>('/ws/chat', {
    onOpen: (attempt) => {
      // Anything sent while we were away only exists in the database.
      if (attempt === 0) return
      void queryClient.invalidateQueries({ queryKey: conversationKeys.list })
      if (activeId) {
        void queryClient.invalidateQueries({ queryKey: conversationKeys.messages(activeId) })
      }
    },
    onMessage: (event) => {
      // `/ws/chat` pushes exactly two frames: `message` and `error`. Read
      // receipts are not pushed -- `POST /conversations/{id}/read` is the only
      // way a thread is marked read, and it refetches on its own.
      if (event.type === 'message') {
        appendMessage(event.data)
        void queryClient.invalidateQueries({ queryKey: conversationKeys.list })
      } else {
        toast.error('Message not delivered', event.data.detail)
      }
    },
  })

  const sendOverHttp = useMutation({
    mutationFn: (content: string) => {
      const payload: MessageCreate = { content }
      return api.post<Message>(`/conversations/${activeId ?? ''}/messages`, payload)
    },
    onSuccess: (message) => {
      appendMessage(message)
      void queryClient.invalidateQueries({ queryKey: conversationKeys.list })
    },
    onError: (error: unknown) => toast.fromError(error, 'Could not send that message'),
  })

  // Opening a thread clears its unread badge (best effort -- a failure here
  // must not break the thread).
  useEffect(() => {
    if (!activeId) return
    let cancelled = false
    api
      .post<void>(`/conversations/${activeId}/read`)
      .then(() => {
        if (!cancelled) void queryClient.invalidateQueries({ queryKey: conversationKeys.list })
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [activeId, queryClient])

  // Stick to the bottom as the thread grows.
  useEffect(() => {
    const element = scrollRef.current
    if (element) element.scrollTop = element.scrollHeight
  }, [messages.length, activeId])

  const handleSend = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    const content = draft.trim()
    if (!content || !activeId) return

    const frame: ChatSocketSend = { conversation_id: activeId, content }
    setDraft('')
    if (!socket.send(frame)) sendOverHttp.mutate(content)
  }

  const connectionNotice =
    socket.status === 'reconnecting'
      ? 'Reconnecting…'
      : socket.status === 'connecting'
        ? 'Connecting…'
        : socket.status === 'closed'
          ? 'Offline — messages will send over HTTP'
          : null

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-900">Messages</h1>
          <p className="mt-1 text-sm text-slate-500">
            Arrange a handoff, agree a price, ask what edition it is.
          </p>
        </div>
        {connectionNotice ? (
          <span className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
            <Spinner size="xs" label={null} />
            {connectionNotice}
          </span>
        ) : null}
      </header>

      <div className="grid gap-4 lg:grid-cols-[20rem_minmax(0,1fr)]">
        {/* ---- inbox ----------------------------------------------------- */}
        <section
          aria-label="Conversations"
          className={cx(
            'card flex h-[70vh] flex-col overflow-hidden',
            activeId && 'hidden lg:flex',
          )}
        >
          <div className="border-b border-slate-100 px-4 py-3">
            <h2 className="text-sm font-semibold text-slate-900">Conversations</h2>
          </div>

          {conversationsQuery.isPending ? (
            <div className="flex flex-1 items-center justify-center">
              <Spinner size="md" />
            </div>
          ) : conversationsQuery.isError ? (
            <div className="p-4">
              <ErrorBox
                error={conversationsQuery.error}
                onRetry={() => void conversationsQuery.refetch()}
              />
            </div>
          ) : conversations.length === 0 ? (
            <EmptyState
              variant="inline"
              className="flex-1"
              title="No conversations yet"
              description="Open a listing and use “Message the uploader” to start one."
              action={
                <Link to="/" className="btn btn-ghost">
                  Browse materials
                </Link>
              }
            />
          ) : (
            <ul className="flex-1 divide-y divide-slate-100 overflow-y-auto">
              {conversations.map((conversation) => (
                <ConversationRow
                  key={conversation.conversation_id}
                  conversation={conversation}
                  active={conversation.conversation_id === activeId}
                />
              ))}
            </ul>
          )}
        </section>

        {/* ---- thread ---------------------------------------------------- */}
        <section
          aria-label="Conversation"
          className={cx('card flex h-[70vh] flex-col overflow-hidden', !activeId && 'hidden lg:flex')}
        >
          {!activeId ? (
            <EmptyState
              variant="inline"
              className="flex-1"
              title="Pick a conversation"
              description="Choose a thread on the left to read it and reply."
            />
          ) : (
            <>
              <div className="flex items-center gap-3 border-b border-slate-100 px-4 py-3">
                <Button
                  variant="ghost"
                  size="sm"
                  className="lg:hidden"
                  onClick={() => navigate('/messages')}
                >
                  Back
                </Button>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-slate-900">
                    {active ? (
                      <Link to={`/users/${active.other_user_id}`} className="hover:text-brand-700">
                        {active.other_user_name ?? 'A student'}
                      </Link>
                    ) : (
                      'Conversation'
                    )}
                  </p>
                  {active?.listing_id ? (
                    <Link
                      to={`/materials/${active.listing_id}`}
                      className="truncate text-xs font-medium text-brand-700 hover:underline"
                    >
                      {active.listing_title ?? 'View the listing'}
                    </Link>
                  ) : null}
                </div>
              </div>

              <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto bg-slate-50/60 p-4">
                {messagesQuery.isPending ? (
                  <PageSpinner label="Loading the conversation" />
                ) : messagesQuery.isError ? (
                  <ErrorBox
                    error={messagesQuery.error}
                    onRetry={() => void messagesQuery.refetch()}
                  />
                ) : messages.length === 0 ? (
                  <p className="py-10 text-center text-sm text-slate-500">
                    No messages yet. Say hello and ask whether the material is still available.
                  </p>
                ) : (
                  messages.map((message, index) => {
                    const mine = message.sender_id === user?.user_id
                    const previous = index > 0 ? messages[index - 1] : null
                    const showDay =
                      !previous || formatDate(previous.timestamp) !== formatDate(message.timestamp)

                    return (
                      <div key={message.message_id}>
                        {showDay ? (
                          <p className="my-3 text-center text-xs font-medium text-slate-400">
                            {formatDate(message.timestamp)}
                          </p>
                        ) : null}
                        <div className={cx('flex', mine ? 'justify-end' : 'justify-start')}>
                          <div
                            className={cx(
                              'max-w-[80%] rounded-2xl px-3.5 py-2 text-sm leading-relaxed shadow-sm',
                              mine
                                ? 'rounded-br-sm bg-brand-600 text-white'
                                : 'rounded-bl-sm bg-white text-slate-800 ring-1 ring-slate-200',
                            )}
                          >
                            <p className="whitespace-pre-wrap break-words">{message.content}</p>
                            <p
                              className={cx(
                                'mt-1 text-right text-[10px]',
                                mine ? 'text-brand-100' : 'text-slate-400',
                              )}
                            >
                              {formatTime(message.timestamp)}
                              {mine && message.read_at ? ' · Read' : ''}
                            </p>
                          </div>
                        </div>
                      </div>
                    )
                  })
                )}
              </div>

              <form onSubmit={handleSend} className="flex items-end gap-2 border-t border-slate-100 p-3">
                <label htmlFor="message-draft" className="sr-only">
                  Message
                </label>
                <textarea
                  id="message-draft"
                  rows={1}
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault()
                      event.currentTarget.form?.requestSubmit()
                    }
                  }}
                  placeholder="Write a message…  (Enter to send, Shift+Enter for a new line)"
                  className="input max-h-32 min-h-[2.5rem] resize-y py-2"
                />
                <Button type="submit" disabled={!draft.trim()} loading={sendOverHttp.isPending}>
                  Send
                </Button>
              </form>
            </>
          )}
        </section>
      </div>
    </div>
  )
}
