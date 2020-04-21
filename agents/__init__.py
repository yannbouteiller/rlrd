import atexit
import gc
import json
import os
import shutil
import tempfile
import time
from os.path import exists
from random import randrange
from tempfile import mkdtemp

import pandas as pd
import yaml

from agents.envs import AvenueEnv
from agents.util import partial, save_json, partial_to_dict, partial_from_dict, load_json, dump, load, git_info
from agents.training import Training
import agents.rtac
import agents.rrtac
import agents.sac
import agents.sac_nstep


def iterate_episodes(run_cls: type = Training, checkpoint_path: str = None):
	"""Generator [1] yielding episode statistics (list of pd.Series) while running and checkpointing
	- run_cls: can by any callable that outputs an appropriate run object (e.g. has a 'run_epoch' method)

	[1] https://docs.python.org/3/howto/functional.html#generators
	"""
	checkpoint_path = checkpoint_path or tempfile.mktemp("_remove_on_exit")

	try:
		if not exists(checkpoint_path):
			print("=== specification ".ljust(70, "="))
			print(yaml.dump(partial_to_dict(run_cls), indent=3, default_flow_style=False, sort_keys=False), end="")
			run_instance = run_cls()
			dump(run_instance, checkpoint_path)
			print("")
		else:
			print("\ncontinuing...\n")

		run_instance = load(checkpoint_path)
		while run_instance.epoch < run_instance.epochs:
			# time.sleep(1)  # on network file systems writing files is asynchronous and we need to wait for sync
			yield run_instance.run_epoch()  # yield stats data frame (this makes this function a generator)
			print("")
			dump(run_instance, checkpoint_path)

			# we delete and reload the run_instance from disk to ensure the exact same code runs regardless of interruptions
			del run_instance
			gc.collect()
			run_instance = load(checkpoint_path)

	finally:
		if checkpoint_path.endswith("_remove_on_exit") and exists(checkpoint_path):
			os.remove(checkpoint_path)


def log_environment_variables():
	"""add certain relevant environment variables to our config
	usage: `LOG_VARIABLES='HOME JOBID' python ...`
	"""
	return {k: os.environ.get(k, '') for k in os.environ.get('LOG_VARIABLES', '').strip().split()}


def run(run_cls: type = Training, checkpoint_path: str = None):
	list(iterate_episodes(run_cls, checkpoint_path))


def run_wandb(entity, project, run_id, run_cls: type = Training, checkpoint_path: str = None):
	"""run and save config and stats to https://wandb.com"""
	wandb_dir = mkdtemp()  # prevent wandb from polluting the home directory
	atexit.register(shutil.rmtree, wandb_dir, ignore_errors=True)  # clean up after wandb atexit handler finishes
	import wandb
	config = partial_to_dict(run_cls)
	config['seed'] = config['seed'] or randrange(1, 1000000)  # if seed == 0 replace with random
	config['environ'] = log_environment_variables()
	config['git'] = git_info()
	resume = checkpoint_path and exists(checkpoint_path)
	wandb.init(dir=wandb_dir, entity=entity, project=project, id=run_id, resume=resume, config=config)
	for stats in iterate_episodes(run_cls, checkpoint_path):
		[wandb.log(json.loads(s.to_json())) for s in stats]


def run_fs(path: str, run_cls: type = Training):
	"""run and save config and stats to `path` (with pickle)"""
	if not exists(path):
		os.mkdir(path)
	save_json(partial_to_dict(run_cls), path + '/spec.json')
	if not exists(path + '/stats'):
		dump(pd.DataFrame(), path + '/stats')
	for stats in iterate_episodes(run_cls, path + '/state'):
		dump(load(path + '/stats').append(stats, ignore_index=True),
             path + '/stats')  # concat with stats from previous episodes


# === specifications ===================================================================================================

TestTraining = partial(
	Training,
	epochs=3,
	rounds=5,
	steps=10,
	Agent=partial(memory_size=1000000),
	Env=partial(id="Pendulum-v0"),
)

SacTraining = partial(
	Training,
	Agent=partial(agents.sac.Agent),
	Env=partial(id="Pendulum-v0"),
	Test=partial(number=1, workers=1),
)

SacNstepTraining = partial(
	Training,
	Agent=partial(agents.sac_nstep.Agent),
	Env=partial(id="Pendulum-v0", store_env=True),
	#Test=partial(number=1, workers=1),
)


RtacTraining = partial(
	SacTraining,
	Agent=partial(agents.rtac.Agent),
	Env=partial(real_time=True),
	Test=partial(number=1, workers=1),
)

SacAvenueTraining = partial(
	Training,
	epochs=20,
	rounds=10,
	steps=5000,
	Agent=partial(agents.sac.AvenueAgent),
	Env=partial(AvenueEnv, real_time=False),
)

RtacAvenueTraining = partial(
	SacAvenueTraining,
	Agent=partial(agents.rtac.AvenueAgent),
	Env=partial(real_time=True),
)

SacAvenueHdTraining = partial(
	Training,
	epochs=20,
	rounds=10,
	steps=5000,
	Agent=partial(agents.sac.AvenueAgent, training_steps=1 / 4, batchsize=32, memory_size=100000,
	              Model=partial(Conv=agents.nn.hd_conv)),
	Env=partial(AvenueEnv, real_time=0, width=368, height=368),
	Test=partial(number=0),  # laptop can't handle more than that
)

RrtacTraining = partial(
	SacTraining,
	Agent=partial(agents.rrtac.Agent, batchsize=32, history_length=8),
	Env=partial(real_time=True),
	Test=partial(number=1, workers=1),
)

# === tests ============================================================================================================
if __name__ == "__main__":
	run(TestTraining)
	# run(RrtacTraining)
