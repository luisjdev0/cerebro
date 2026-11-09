import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// In dev the portal talks to a real gateway: set VITE_GATEWAY_URL (e.g. the test
// server's http://192.168.0.101:8090). In production the portal is served by the
// gateway itself, so every API call is same-origin and no proxy is needed.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const target = env.VITE_GATEWAY_URL;
  const proxy = target
    ? Object.fromEntries(
        ['/api', '/mcp'].map((p) => [p, { target, changeOrigin: true }]),
      )
    : undefined;
  return {
    plugins: [react(), tailwindcss()],
    server: { proxy, allowedHosts: true },
  };
});
