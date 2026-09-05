import json
import random
import numpy as np
from game import run_one_game
from agent_baselines import StaticAgent, RandomAgent, GreedyAgent, AttitudeAgent
from agent_48 import StudentAgent

# This file provides an example to simulate one game and export the game process for visualization.

if __name__ == "__main__":

	agents_dict = {
		'AUSTRIA': StudentAgent(),
		'ENGLAND': GreedyAgent(),
		'FRANCE': GreedyAgent(),
		'GERMANY': GreedyAgent(),
		'ITALY': GreedyAgent(),
		'RUSSIA': GreedyAgent(),
		'TURKEY': GreedyAgent()
	}

	save_file = 'game_for_vis.json'
	run_one_game(agents_dict, save_file=save_file)

	# Save which agent class played each power, so make_viewer.py can show a
	# "who is who" legend alongside the country colours. This is written to a
	# sidecar file (rather than into game_for_vis.json) since the saved game
	# format itself has no field for it.
	agents_file = save_file.rsplit('.', 1)[0] + '_agents.json'
	with open(agents_file, 'w', encoding='utf-8') as f:
		json.dump({power: type(agent).__name__ for power, agent in agents_dict.items()}, f, indent=2)

	'''
	A JSON file will be saved, which can be visualized using the Web Interface provided on https://github.com/diplomacy/diplomacy?tab=readme-ov-file#web-interface
	
	Follow the instructions to setup the Web Interface: https://github.com/diplomacy/diplomacy?tab=readme-ov-file#web-interface, which may take some time. 

	Then click 'load a game from the disk' on the Web Interface, to load and visualize the saved JSON.

	This visualization is not necessary for completing the project. It is mainly to assist the debugging and for fun.

	Note that if there is an existing file with the same name, the game data will be appended to the same file, and causing errors for visualization.
	'''
 
