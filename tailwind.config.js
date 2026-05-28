/**
 * Tailwind config for WORLDSCOPE.
 *
 * Heritage palette mapped onto Tailwind theme.extend.colors AND onto a custom
 * daisyUI theme so component primitives (btn, card, badge, navbar) inherit the
 * brand without per-call overrides.
 *
 * Content scan covers the rendered HTML (dist/) and the Python files that emit
 * it (tools/, worldscope/), so any utility class referenced inside a Python
 * string literal still survives the production purge.
 */
module.exports = {
  content: [
    './dist/**/*.html',
    './tools/**/*.py',
    './worldscope/**/*.py',
  ],
  safelist: [
    // daisyUI dynamic classes that may come from Python string composition
    { pattern: /^(badge|btn|card|navbar|breadcrumbs|prose)/ },
    { pattern: /^(bg|text|border)-(parchment|navy|gold|teal|crimson|carolina|slate|mist|ink)/ },
  ],
  theme: {
    extend: {
      colors: {
        parchment: '#FAF8F3',
        navy: {
          DEFAULT: '#13294B',
          soft: '#1F3D6E',
        },
        gold: {
          DEFAULT: '#D4A017',
          soft: '#E8BC42',
        },
        teal: '#1A8A87',
        crimson: '#990000',
        carolina: '#4B9CD3',
        slate: {
          DEFAULT: '#4E5667',
          dim: '#6B7180',
        },
        mist: '#E8E2D5',
        ink: '#0B1220',
      },
      fontFamily: {
        serif: [
          '"Source Serif 4"',
          '"Source Serif Pro"',
          'Georgia',
          '"Iowan Old Style"',
          'serif',
        ],
        sans: [
          'Inter',
          '-apple-system',
          'BlinkMacSystemFont',
          '"Helvetica Neue"',
          'Arial',
          'sans-serif',
        ],
        mono: [
          '"JetBrains Mono"',
          '"SF Mono"',
          'Menlo',
          'Consolas',
          'monospace',
        ],
      },
      boxShadow: {
        card: '0 1px 2px rgba(11,18,32,0.04), 0 4px 12px rgba(11,18,32,0.05)',
        lift: '0 2px 6px rgba(11,18,32,0.06), 0 12px 28px rgba(11,18,32,0.10)',
      },
      typography: (theme) => ({
        slate: {
          css: {
            '--tw-prose-body': theme('colors.ink'),
            '--tw-prose-headings': theme('colors.navy.DEFAULT'),
            '--tw-prose-links': theme('colors.navy.DEFAULT'),
            '--tw-prose-bold': theme('colors.ink'),
            '--tw-prose-quotes': theme('colors.slate.DEFAULT'),
            '--tw-prose-quote-borders': theme('colors.gold.DEFAULT'),
            '--tw-prose-code': theme('colors.crimson'),
            '--tw-prose-bullets': theme('colors.slate.dim'),
            '--tw-prose-counters': theme('colors.slate.dim'),
            'a': {
              textDecoration: 'none',
              borderBottom: '1px solid rgba(19,41,75,0.25)',
              transition: 'border-color 0.15s, color 0.15s',
            },
            'a:hover': {
              borderBottomColor: theme('colors.gold.DEFAULT'),
              color: theme('colors.navy.DEFAULT'),
            },
            'h2': {
              borderBottom: `1px solid ${theme('colors.mist')}`,
              paddingBottom: '0.35em',
              letterSpacing: '-0.01em',
            },
            'blockquote': {
              fontStyle: 'normal',
              borderLeftWidth: '3px',
              borderLeftColor: theme('colors.gold.DEFAULT'),
              backgroundColor: 'rgba(232,226,213,0.35)',
              paddingTop: '0.6em',
              paddingBottom: '0.6em',
              borderRadius: '0 4px 4px 0',
            },
            'code': {
              fontWeight: '500',
              backgroundColor: 'rgba(232,226,213,0.55)',
              padding: '0.1em 0.35em',
              borderRadius: '3px',
            },
            'code::before': { content: '""' },
            'code::after': { content: '""' },
            'table': {
              fontFamily: theme('fontFamily.sans').join(', '),
              fontSize: '0.95em',
            },
            'th': {
              backgroundColor: theme('colors.mist'),
              color: theme('colors.navy.DEFAULT'),
            },
          },
        },
      }),
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
    require('@tailwindcss/forms'),
    require('@tailwindcss/aspect-ratio'),
    require('daisyui'),
  ],
  daisyui: {
    themes: [
      {
        heritage: {
          'color-scheme': 'light',
          primary: '#13294B',
          'primary-content': '#FAF8F3',
          secondary: '#1A8A87',
          'secondary-content': '#FAF8F3',
          accent: '#D4A017',
          'accent-content': '#0B1220',
          neutral: '#4E5667',
          'neutral-content': '#FAF8F3',
          'base-100': '#FAF8F3',
          'base-200': '#F2EDE0',
          'base-300': '#E8E2D5',
          'base-content': '#0B1220',
          info: '#4B9CD3',
          'info-content': '#0B1220',
          success: '#1A8A87',
          'success-content': '#FAF8F3',
          warning: '#D4A017',
          'warning-content': '#0B1220',
          error: '#990000',
          'error-content': '#FAF8F3',
          '--rounded-box': '0.5rem',
          '--rounded-btn': '0.375rem',
          '--rounded-badge': '0.25rem',
          '--tab-radius': '0.375rem',
        },
      },
    ],
    base: false,        // we own base styles via @layer base
    styled: true,
    utils: true,
    logs: false,
    themeRoot: ':root',
  },
};
