import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { ApiError, api, clearTokens, getRefreshToken, getToken, setTokens } from './api'
import type { LoginRequest, RegisterRequest, TokenPair, User } from './types'

/**
 * Session state for the whole app.
 *
 * The access token in localStorage is the source of truth for "am I logged
 * in?"; `user` is what the token resolves to. On mount, a stored token is
 * verified by fetching `/users/me` -- a token that no longer works must not
 * leave the UI showing a logged-in shell.
 */
export interface AuthContextValue {
  user: User | null
  /** True until the initial `/users/me` check settles. Gate routes on this. */
  loading: boolean
  login: (email: string, password: string) => Promise<User>
  register: (payload: RegisterRequest) => Promise<User>
  logout: () => Promise<void>
  refreshUser: () => Promise<User | null>
  isAdmin: boolean
}

const AuthContext = createContext<AuthContextValue | null>(null)

/**
 * The campus scope shown in the header (FR 4.3). `null` means "all campuses".
 * It lives beside the session because it defaults to the user's own campus and
 * is read by browse, the request board, the leaderboard and trending.
 */
export interface CampusFilterValue {
  campusId: string | null
  setCampusId: (campusId: string | null) => void
}

const CampusFilterContext = createContext<CampusFilterValue | null>(null)

const CAMPUS_KEY = 'notevault.campus'
/** Sentinel stored for "all campuses", so it survives a reload. */
const ALL_CAMPUSES = '__all__'

function readStoredCampus(): string | null | undefined {
  try {
    const raw = window.localStorage.getItem(CAMPUS_KEY)
    if (raw === null) return undefined
    return raw === ALL_CAMPUSES ? null : raw
  } catch {
    return undefined
  }
}

function writeStoredCampus(campusId: string | null): void {
  try {
    window.localStorage.setItem(CAMPUS_KEY, campusId ?? ALL_CAMPUSES)
  } catch {
    /* storage is optional */
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState<boolean>(() => Boolean(getToken()))
  const [campusId, setCampusIdState] = useState<string | null>(() => readStoredCampus() ?? null)
  const [campusTouched, setCampusTouched] = useState<boolean>(() => readStoredCampus() !== undefined)

  const fetchMe = useCallback(async (): Promise<User | null> => {
    try {
      const me = await api.get<User>('/users/me')
      setUser(me)
      return me
    } catch (error) {
      // 401 means the stored token is dead; anything else (server down) should
      // not silently sign the user out, but there is nothing to show either.
      if (error instanceof ApiError && error.status === 401) clearTokens()
      setUser(null)
      return null
    }
  }, [])

  // Resolve a stored token exactly once on mount.
  useEffect(() => {
    let cancelled = false
    if (!getToken()) {
      setLoading(false)
      return
    }
    void fetchMe().finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [fetchMe])

  // Until the user picks a campus themselves, follow their own campus.
  useEffect(() => {
    if (!campusTouched && user) setCampusIdState(user.campus_id)
  }, [campusTouched, user])

  const setCampusId = useCallback((next: string | null) => {
    setCampusTouched(true)
    setCampusIdState(next)
    writeStoredCampus(next)
  }, [])

  const login = useCallback(
    async (email: string, password: string): Promise<User> => {
      const payload: LoginRequest = { email, password }
      const tokens = await api.post<TokenPair>('/auth/login', payload, { auth: false })
      setTokens(tokens)
      queryClient.clear()
      const me = await api.get<User>('/users/me')
      setUser(me)
      setLoading(false)
      return me
    },
    [queryClient],
  )

  const register = useCallback(
    async (payload: RegisterRequest): Promise<User> => {
      // Registration either hands back a token pair or just the created user;
      // accept both rather than coupling the UI to one of them.
      const result = await api.post<TokenPair | User>('/auth/register', payload, { auth: false })
      if (result && typeof result === 'object' && 'access_token' in result) {
        setTokens(result)
        queryClient.clear()
        const me = await api.get<User>('/users/me')
        setUser(me)
        setLoading(false)
        return me
      }
      return login(payload.email, payload.password)
    },
    [login, queryClient],
  )

  const logout = useCallback(async (): Promise<void> => {
    const refresh_token = getRefreshToken()
    try {
      // Best effort: the server revokes the refresh token, but a failure here
      // must never leave the user stuck in a session they asked to end.
      if (refresh_token) await api.post<void>('/auth/logout', { refresh_token })
    } catch {
      /* ignore */
    } finally {
      clearTokens()
      setUser(null)
      setLoading(false)
      queryClient.clear()
    }
  }, [queryClient])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      loading,
      login,
      register,
      logout,
      refreshUser: fetchMe,
      isAdmin: user?.role === 'ADMIN',
    }),
    [user, loading, login, register, logout, fetchMe],
  )

  const campusValue = useMemo<CampusFilterValue>(
    () => ({ campusId, setCampusId }),
    [campusId, setCampusId],
  )

  return (
    <AuthContext.Provider value={value}>
      <CampusFilterContext.Provider value={campusValue}>{children}</CampusFilterContext.Provider>
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}

/** Header campus scope (FR 4.3). `campusId === null` means all campuses. */
export function useCampusFilter(): CampusFilterValue {
  const ctx = useContext(CampusFilterContext)
  if (!ctx) throw new Error('useCampusFilter must be used inside <AuthProvider>')
  return ctx
}
