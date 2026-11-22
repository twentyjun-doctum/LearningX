# Neon Auth client bundle

`assets/vendor/neon-auth.js` is `@neon/auth` 0.8.2 (Apache-2.0, licenses kept at the end of the file) bundled
into one IIFE because the static pages have no bundler and the CSP only allows scripts from `'self'`.
The teacher page loads it lazily, only when Google sign-in is used.

Rebuild: `cd frontend/tools/neon-auth && npm install && npm run build`
