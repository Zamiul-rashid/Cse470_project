/**
 * NoteVault design tokens.
 *
 * This file is the single source of colour truth: brand colors, the four
 * listing-type colors, and the status palette all live here. No ad-hoc hex
 * values in components. If a screen needs a
 * colour that is not here, add it here first.
 *
 * The `listing` and `state` scales are flat (one hex per token) and are meant
 * to be used with Tailwind's opacity modifier for the soft variants, e.g.
 *   bg-listing-rent/10  text-listing-rent  ring-listing-rent/20
 * The class strings themselves are centralised in src/lib/format.ts so the JIT
 * scanner always sees them literally.
 *
 * @type {import('tailwindcss').Config}
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Primary brand ramp -- teal. brand.600 is the primary action colour.
        brand: {
          50: '#f0fdfa',
          100: '#ccfbf1',
          200: '#99f6e4',
          300: '#5eead4',
          400: '#2dd4bf',
          500: '#14b8a6',
          600: '#0d9488',
          700: '#0f766e',
          800: '#115e59',
          900: '#134e4a',
        },
        // FR 1.3 -- the four listing types. Distinct hues, all AA on white.
        listing: {
          sell: '#1d4ed8',      // blue   -- money changes hands
          rent: '#7c3aed',      // violet -- time-bounded
          exchange: '#b45309',  // amber  -- barter
          free: '#047857',      // green  -- giveaway
        },
        // Listing / transaction / request / report status palette (section 4).
        state: {
          pending: '#b45309',
          approved: '#047857',
          rejected: '#b91c1c',
          reserved: '#1d4ed8',
          completed: '#4338ca',
          removed: '#52525b',
        },
      },
      fontFamily: {
        sans: [
          'Inter',
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          'Segoe UI',
          'Roboto',
          'Helvetica Neue',
          'Arial',
          'Noto Sans',
          'sans-serif',
        ],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 2px 0 rgb(15 23 42 / 0.04), 0 1px 3px 0 rgb(15 23 42 / 0.06)',
        pop: '0 8px 24px -6px rgb(15 23 42 / 0.16)',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        'fade-in': 'fade-in 120ms ease-out',
        'slide-up': 'slide-up 160ms ease-out',
      },
    },
  },
  plugins: [],
}
