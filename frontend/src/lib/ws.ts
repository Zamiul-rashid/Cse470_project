/**
 * Reconnecting WebSocket hooks.
 *
 * The socket is never the only delivery path. Chat
 * history and the notification list both exist as REST endpoints, so a dropped
 * socket degrades to "stale until refetch" rather than "broken". That is why
 * the reconnect handler also invalidates the relevant queries -- reconnecting
 * re-pulls whatever was missed while offline.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { getToken, wsUrl } from './api'
import type { Notification, NotificationSocketEvent } from './types'

export type SocketStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

/** 1s, 2s, 4s, 8s, then 15s forever. */
const BACKOFF_MS = [1000, 2000, 4000, 8000] as const
const BACKOFF_CAP_MS = 15000

function backoffFor(attempt: number): number {
  return BACKOFF_MS[attempt] ?? BACKOFF_CAP_MS
}

/** Closed deliberately (logout, unmount) -- no reconnect. */
const NORMAL_CLOSURE = 1000
/** The server's "your token is not valid" code, from the WS auth guard. */
const AUTH_FAILURE = 4401

export interface UseSocketOptions<TEvent> {
  /** Parsed JSON frame. Non-JSON frames are ignored. */
  onMessage?: (event: TEvent) => void
  /** Fired on every successful (re)connect -- the place to re-pull history. */
  onOpen?: (attempt: number) => void
  onClose?: (event: CloseEvent) => void
  /** Default true. Set false while logged out to keep the socket shut. */
  enabled?: boolean
}

export interface SocketHandle {
  status: SocketStatus
  /** Serialises to JSON. Returns false if the socket is not open. */
  send: (payload: unknown) => boolean
  /** Force an immediate reconnect, resetting the backoff. */
  reconnect: () => void
}

/**
 * One socket per (path, enabled) pair, with exponential-backoff reconnect and
 * a clean teardown. Handlers are held in refs so passing inline callbacks does
 * not tear the connection down on every render.
 */
export function useSocket<TEvent = unknown>(
  path: string,
  options: UseSocketOptions<TEvent> = {},
): SocketHandle {
  const { enabled = true } = options
  const [status, setStatus] = useState<SocketStatus>('idle')

  const socketRef = useRef<WebSocket | null>(null)
  const timerRef = useRef<number | null>(null)
  const attemptRef = useRef(0)
  const disposedRef = useRef(false)

  const handlers = useRef(options)
  handlers.current = options

  const send = useCallback((payload: unknown): boolean => {
    const socket = socketRef.current
    if (!socket || socket.readyState !== WebSocket.OPEN) return false
    socket.send(typeof payload === 'string' ? payload : JSON.stringify(payload))
    return true
  }, [])

  useEffect(() => {
    if (!enabled) {
      setStatus('idle')
      return
    }

    disposedRef.current = false
    attemptRef.current = 0

    const clearTimer = (): void => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
        timerRef.current = null
      }
    }

    const connect = (): void => {
      if (disposedRef.current) return

      const token = getToken()
      if (!token) {
        // No credentials to hand the handshake; wait and look again.
        setStatus('reconnecting')
        clearTimer()
        timerRef.current = window.setTimeout(connect, backoffFor(attemptRef.current++))
        return
      }

      setStatus(attemptRef.current === 0 ? 'connecting' : 'reconnecting')

      let socket: WebSocket
      try {
        socket = new WebSocket(wsUrl(path, token))
      } catch {
        clearTimer()
        timerRef.current = window.setTimeout(connect, backoffFor(attemptRef.current++))
        return
      }
      socketRef.current = socket

      socket.onopen = () => {
        if (disposedRef.current) {
          socket.close(NORMAL_CLOSURE)
          return
        }
        const attempt = attemptRef.current
        attemptRef.current = 0
        setStatus('open')
        handlers.current.onOpen?.(attempt)
      }

      socket.onmessage = (event: MessageEvent<string>) => {
        if (disposedRef.current) return
        let parsed: TEvent
        try {
          parsed = JSON.parse(event.data) as TEvent
        } catch {
          return // the server only ever sends JSON frames
        }
        handlers.current.onMessage?.(parsed)
      }

      socket.onerror = () => {
        // `onclose` always follows; the retry is scheduled there.
      }

      socket.onclose = (event: CloseEvent) => {
        socket.onopen = null
        socket.onmessage = null
        socket.onerror = null
        socket.onclose = null
        if (socketRef.current === socket) socketRef.current = null

        handlers.current.onClose?.(event)
        if (disposedRef.current) return

        // A rejected token will be rejected again in one second, and again in
        // two. Stop; the 401 path in api.ts owns re-authentication.
        if (event.code === AUTH_FAILURE) {
          setStatus('closed')
          return
        }

        setStatus('reconnecting')
        clearTimer()
        timerRef.current = window.setTimeout(connect, backoffFor(attemptRef.current++))
      }
    }

    connect()

    return () => {
      disposedRef.current = true
      clearTimer()
      const socket = socketRef.current
      socketRef.current = null
      if (socket) {
        socket.onopen = null
        socket.onmessage = null
        socket.onerror = null
        socket.onclose = null
        if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
          socket.close(NORMAL_CLOSURE)
        }
      }
      setStatus('idle')
    }
  }, [path, enabled])

  const reconnect = useCallback(() => {
    const socket = socketRef.current
    attemptRef.current = 0
    if (socket) socket.close() // onclose schedules the retry immediately
  }, [])

  return { status, send, reconnect }
}

/** Query keys the notification socket keeps fresh. */
export const notificationKeys = {
  all: ['notifications'] as const,
  list: (params?: object) => ['notifications', 'list', params ?? {}] as const,
}

export interface UseNotificationSocketOptions {
  /** Fired for each pushed notification -- toast it, play a sound, whatever. */
  onNotification?: (notification: Notification) => void
  enabled?: boolean
}

/**
 * `/ws/notifications` -- server push only (FR 2.3, 3.2).
 *
 * Every frame invalidates the notification queries, so the bell badge and the
 * list stay correct even if a frame is missed: the socket is a hint to refetch,
 * not the record itself.
 */
export function useNotificationSocket(options: UseNotificationSocketOptions = {}): SocketHandle {
  const { onNotification, enabled = true } = options
  const queryClient = useQueryClient()

  return useSocket<NotificationSocketEvent>('/ws/notifications', {
    enabled,
    onOpen: (attempt) => {
      // Anything that arrived while we were offline is only in the database.
      if (attempt > 0) void queryClient.invalidateQueries({ queryKey: notificationKeys.all })
    },
    onMessage: (event) => {
      void queryClient.invalidateQueries({ queryKey: notificationKeys.all })
      // The server frames a push as { kind: 'notification', notification: {...} }
      // -- see services/notifier._frame. It mirrors NotificationRead field for
      // field so a pushed frame can go straight into the list rendered from REST.
      if (event.kind === 'notification') onNotification?.(event.notification)
    },
  })
}
