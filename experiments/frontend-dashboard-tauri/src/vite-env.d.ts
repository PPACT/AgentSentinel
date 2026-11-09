/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_AGENT_SENTINEL_ROOT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
