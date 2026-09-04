import argparse
import html
import json
import os

from diplomacy.engine.game import Game
from diplomacy.engine.renderer import Renderer
from diplomacy.utils.game_phase_data import GamePhaseData

# Builds a single, self-contained HTML file that lets you step through every
# phase of a game exported by visualize.py (via run_one_game(..., save_file=...)).


def load_last_saved_game(json_path):
    """Loads the most recent game record from a saved-game file.

    diplomacy.utils.export.to_saved_game_format appends one JSON line per
    call, so a file that was written to across multiple runs contains
    several full game records (one per line). We only want the latest one.
    """
    with open(json_path, 'r', encoding='utf-8') as file:
        lines = [line for line in file if line.strip()]

    if not lines:
        raise ValueError(f'{json_path} is empty.')

    return json.loads(lines[-1])


def render_phase_to_svg(saved_game, phase_dict):
    """Rebuilds the game state for a single phase and renders it to SVG."""
    game = Game(
        game_id=saved_game.get('id'),
        map_name=saved_game.get('map', 'standard'),
        rules=saved_game.get('rules', []),
    )
    game.set_phase_data(GamePhaseData.from_dict(phase_dict), clear_history=True)

    svg = Renderer(game).render(incl_orders=True, incl_abbrev=False)

    # Renderer output includes an XML prolog + DOCTYPE referencing svg.dtd,
    # which browsers do not need (and may otherwise try to resolve) when the
    # SVG is embedded inline. Keep only the <svg ...>...</svg> element.
    start = svg.find('<svg')
    return svg[start:] if start != -1 else svg


def build_html(saved_game, svgs_by_phase):
    phase_names = [phase['name'] for phase in saved_game.get('phases', [])]

    slides = '\n'.join(
        f'<div class="phase" id="phase-{i}" style="display:none">{svg}</div>'
        for i, svg in enumerate(svgs_by_phase)
    )
    phase_names_json = json.dumps(phase_names)
    game_id = html.escape(str(saved_game.get('id', '')))
    map_name = html.escape(str(saved_game.get('map', '')))

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Diplomacy Game Viewer</title>
<style>
  body {{ font-family: sans-serif; margin: 0; padding: 16px; background: #f5f5f0; }}
  #header {{ display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }}
  #phase-label {{ font-size: 1.1em; font-weight: bold; min-width: 220px; }}
  button {{ font-size: 1em; padding: 4px 14px; cursor: pointer; }}
  #map-container {{ max-width: 1200px; margin: 0 auto; background: white; border: 1px solid #ccc; }}
  .phase svg {{ width: 100%; height: auto; display: block; }}
  #slider {{ flex: 1; min-width: 200px; }}
  #meta {{ color: #666; font-size: 0.9em; }}
</style>
</head>
<body>

<div id="header">
  <button id="prev-btn">&larr; Prev</button>
  <button id="play-btn">&#9654; Play</button>
  <button id="next-btn">Next &rarr;</button>
  <select id="speed">
    <option value="2000">0.5x</option>
    <option value="1000" selected>1x</option>
    <option value="500">2x</option>
    <option value="250">4x</option>
  </select>
  <span id="phase-label"></span>
  <input type="range" id="slider" min="0" max="{len(svgs_by_phase) - 1}" value="0">
  <span id="meta">game {game_id} &middot; map: {map_name}</span>
</div>

<div id="map-container">
{slides}
</div>

<script>
  const phaseNames = {phase_names_json};
  const numPhases = phaseNames.length;
  let current = 0;

  const label = document.getElementById('phase-label');
  const slider = document.getElementById('slider');
  const playBtn = document.getElementById('play-btn');
  const speedSelect = document.getElementById('speed');

  let playTimer = null;

  function showPhase(index) {{
    document.getElementById(`phase-${{current}}`).style.display = 'none';
    current = Math.max(0, Math.min(numPhases - 1, index));
    document.getElementById(`phase-${{current}}`).style.display = 'block';
    label.textContent = `Phase ${{current + 1}} / ${{numPhases}}: ${{phaseNames[current]}}`;
    slider.value = current;

    if (current === numPhases - 1) {{
      stopPlaying();
    }}
  }}

  function startPlaying() {{
    if (current === numPhases - 1) {{
      showPhase(0);
    }}
    playBtn.textContent = '⏸ Pause';
    playTimer = setInterval(() => showPhase(current + 1), parseInt(speedSelect.value, 10));
  }}

  function stopPlaying() {{
    clearInterval(playTimer);
    playTimer = null;
    playBtn.textContent = '▶ Play';
  }}

  function togglePlaying() {{
    if (playTimer) {{
      stopPlaying();
    }} else {{
      startPlaying();
    }}
  }}

  document.getElementById('prev-btn').addEventListener('click', () => {{ stopPlaying(); showPhase(current - 1); }});
  document.getElementById('next-btn').addEventListener('click', () => {{ stopPlaying(); showPhase(current + 1); }});
  playBtn.addEventListener('click', togglePlaying);
  speedSelect.addEventListener('change', () => {{ if (playTimer) {{ stopPlaying(); startPlaying(); }} }});
  slider.addEventListener('input', (e) => {{ stopPlaying(); showPhase(parseInt(e.target.value, 10)); }});

  document.addEventListener('keydown', (e) => {{
    if (e.key === 'ArrowLeft') {{ stopPlaying(); showPhase(current - 1); }}
    if (e.key === 'ArrowRight') {{ stopPlaying(); showPhase(current + 1); }}
    if (e.key === ' ') {{ e.preventDefault(); togglePlaying(); }}
  }});

  showPhase(0);
</script>

</body>
</html>
'''


def make_viewer(json_path, output_path):
    saved_game = load_last_saved_game(json_path)
    phases = saved_game.get('phases', [])

    if not phases:
        raise ValueError(f'{json_path} contains no phases to render.')

    svgs_by_phase = [render_phase_to_svg(saved_game, phase) for phase in phases]
    html_content = build_html(saved_game, svgs_by_phase)

    with open(output_path, 'w', encoding='utf-8') as file:
        file.write(html_content)

    print(f'Wrote {len(phases)} phases to {output_path}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Convert a saved Diplomacy game JSON into a viewable HTML file.')
    parser.add_argument('json_path', nargs='?', default='game_for_vis.json', help='Path to the saved game JSON (default: game_for_vis.json)')
    parser.add_argument('output_path', nargs='?', default='game_viewer.html', help='Path to write the HTML viewer to (default: game_viewer.html)')
    args = parser.parse_args()

    make_viewer(args.json_path, args.output_path)
