/// <reference types="vite/client" />

interface ImportMetaEnv {
  // A build-time variable from the local environment file (never committed)
  // and from the Render build environment.
  readonly VITE_AG_GRID_LICENSE_KEY?: string;
}
