// Bundled for Question-up's static pages (no bundler, CSP script-src 'self').
import { createAuthClient } from '@neon/auth';
import { BetterAuthVanillaAdapter } from '@neon/auth/vanilla/adapters';

window.QUNeonAuth = {
  create(url) {
    // The auth service is on another origin: send its session cookie on cross-origin calls.
    return createAuthClient(url, { adapter: BetterAuthVanillaAdapter({ fetchOptions: { credentials: 'include' } }) });
  },
};
