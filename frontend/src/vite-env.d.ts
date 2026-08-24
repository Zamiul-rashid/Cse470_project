/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Origin of the API, without the `/api/v1` prefix. Empty string (the
   * default) means same-origin, which is correct for both the Vite dev proxy
   * and the production build served by Uvicorn.
   */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
