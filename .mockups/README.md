# Design mockups — review artifacts (NOT wired into the build)

Self-contained HTML explorations of a visual redesign for the WORLDSCOPE
brief page, rendered with real 2026-05-31 briefing content. These are for
review/sign-off only — none are referenced by render_brief.py, the Tailwind
config, or the published site. Open directly in a browser.

- option-a-editorial.html — light newsprint dossier; serif headlines, one blue
- option-b-terminal.html  — dark canvas, single teal accent, ticker strip
- option-c-swiss.html      — hard-grid Swiss/brutalist, one signal red
- option-d-refined.html    — refined "mix": Swiss bones + a coded color system
  drawn from Carolina blue / ASU maroon+gold / Indiana crimson; adaptive
  light+dark from one token set (◐ Theme toggle)

Once a direction is signed off, it gets ported into tailwind.config.js +
assets/src/tailwind.input.css so the whole site inherits it from one source.
