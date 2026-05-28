/** WORLDSCOPE design tokens, single source of truth.
 *
 *  Every color, every font, every shadow, every animation key lives
 *  here. Components consume tokens via Tailwind utilities — no more
 *  scattered hex values across page_chrome.py, render.py, individual
 *  asset .js files.
 *
 *  Build: `npm run build:css` (CI runs this before brief.py).
 *  Output: dist/assets/worldscope.css (~30KB minified, tree-shaken
 *  to only the classes that actually appear in source).
 */
const colors = require('tailwindcss/colors');

module.exports = {
  content: [
    './worldscope/**/*.py',
    './tools/**/*.py',
    './dist/**/*.html',
    './dist/**/*.js',
  ],
  // Important: keep the safelist tight; only patterns the Python f-strings
  // generate dynamically that the content-scan can't see.
  safelist: [
    // Severity / state badge color combos
    { pattern: /bg-(ink|panel|parchment|mist|slate|navy|gold|carolina|crimson|teal|sage)(-(soft|deep|dim|mid|strong))?(\/[0-9]+)?/ },
    { pattern: /text-(ink|panel|parchment|mist|slate|navy|gold|carolina|crimson|teal|sage)(-(soft|deep|dim|mid|strong))?(\/[0-9]+)?/ },
    { pattern: /border-(ink|panel|parchment|mist|slate|navy|gold|carolina|crimson|teal|sage)(-(soft|deep|dim|mid|strong))?(\/[0-9]+)?/ },
    { pattern: /border-l-(navy|gold|crimson|teal|sage)/ },
  ],
  theme: {
    extend: {
      colors: {
        // === Surface tokens (light theme — the canonical frosty-white)
        // Frostier than the old parchment; cooler, less buttery.
        canvas:    '#FCFCFD',   // page background (was parchment #FAF8F3)
        panel:     '#FFFFFF',   // card surfaces
        mist:      '#EDEDF0',   // hairlines, dividers, soft fills
        'mist-strong': '#D4D4D8',  // stronger dividers
        ink:       '#0B1220',   // primary text
        slate:     { DEFAULT: '#4E5667', mid: '#6B7280', dim: '#9CA3AF' },

        // === Brand tokens (heritage stays but tuned)
        // Slightly desaturated for the cooler frosty palette.
        navy:      { DEFAULT: '#13294B', soft: '#1F3D6E', deep: '#091428' },
        gold:      { DEFAULT: '#C8961A', soft: '#E0B240' },   // less yellow, more amber
        carolina:  '#4B9CD3',
        crimson:   '#9B1C1C',                                  // less burnt-orange
        teal:      '#0F766E',                                  // deeper, more refined
        sage:      '#5C7B6C',                                  // NEW: positive-quiet status
      },
      fontFamily: {
        // Self-hosted, declared in worldscope/design/typography.css
        serif: ['"Source Serif 4"', 'Source Serif Pro', 'Georgia', 'Iowan Old Style', 'ui-serif', 'serif'],
        sans:  ['"Inter"', 'ui-sans-serif', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto', 'system-ui', 'sans-serif'],
        mono:  ['"Geist Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      fontSize: {
        // Editorial type scale, declared once.
        'kicker':    ['10.5px', { letterSpacing: '0.18em', lineHeight: '1.2', fontWeight: '700' }],
        'editorial': ['clamp(34px, 5vw, 56px)', { lineHeight: '1.04', letterSpacing: '-0.025em', fontWeight: '700' }],
        'display':   ['clamp(28px, 3.4vw, 38px)', { lineHeight: '1.1',  letterSpacing: '-0.018em', fontWeight: '700' }],
        'lede':      ['clamp(16px, 1.2vw, 18px)', { lineHeight: '1.55', fontWeight: '400' }],
      },
      letterSpacing: {
        kicker:     '0.18em',
        kickerwide: '0.22em',
      },
      boxShadow: {
        // Lower, cooler shadows — the frosty aesthetic is "lifted off the page" not "embossed".
        'card':  '0 1px 1px rgba(11,18,32,0.03), 0 2px 6px rgba(11,18,32,0.04)',
        'lift':  '0 1px 2px rgba(11,18,32,0.04), 0 12px 24px rgba(11,18,32,0.08)',
        'globe': '0 30px 60px -20px rgba(11,18,32,0.25), 0 20px 40px -25px rgba(11,18,32,0.12)',
        'inset-soft': 'inset 0 1px 2px rgba(11,18,32,0.04)',
      },
      transitionTimingFunction: {
        editorial: 'cubic-bezier(0.2, 0.7, 0.2, 1)',
        gentle:    'cubic-bezier(0.4, 0, 0.2, 1)',
        spring:    'cubic-bezier(0.34, 1.56, 0.64, 1)',
      },
      keyframes: {
        'fade-rise':   { '0%': { opacity: '0', transform: 'translateY(8px)' },
                         '100%': { opacity: '1', transform: 'translateY(0)' } },
        'fade-in':     { '0%': { opacity: '0' }, '100%': { opacity: '1' } },
        'pulse-soft':  { '0%,100%': { opacity: '0.55' }, '50%': { opacity: '1' } },
        'pulse-ring':  { '0%':   { transform: 'scale(0.85)', opacity: '0.6' },
                         '100%': { transform: 'scale(1.6)',  opacity: '0' } },
        'shimmer':     { '0%': { backgroundPosition: '-200% 0' },
                         '100%': { backgroundPosition: '200% 0' } },
      },
      animation: {
        'fade-rise':   'fade-rise 0.5s cubic-bezier(0.2,0.7,0.2,1) both',
        'fade-in':     'fade-in 0.4s ease-out both',
        'pulse-soft':  'pulse-soft 3s ease-in-out infinite',
        'pulse-ring':  'pulse-ring 2s ease-out infinite',
        'shimmer':     'shimmer 1.6s ease-in-out infinite',
      },
      backdropBlur: {
        xs: '2px',
      },
    },
  },
  plugins: [
    require('@tailwindcss/typography'),
    require('@tailwindcss/forms'),
    require('@tailwindcss/aspect-ratio'),
  ],
};
