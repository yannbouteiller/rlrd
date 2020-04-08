from copy import deepcopy
from dataclasses import dataclass
from functools import reduce

import torch
from agents.envs import AvenueEnv
from torch.nn.functional import mse_loss
import numpy as np

import agents.sac
from agents.memory import Memory, TrajMemory
from agents.nn import no_grad, exponential_moving_average, PopArt
from agents.util import partial
from agents.rtac_models import ConvRTAC, ConvDouble


@dataclass(eq=0)
class Agent(agents.sac.Agent):
  Model: type = agents.rtac_models.MlpDouble
  loss_alpha: float = 0.2
  history_length: int = 16

  def __post_init__(self, observation_space, action_space):
    device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
    model = self.Model(observation_space, action_space)
    self.model = model.to(device)
    self.model_target = no_grad(deepcopy(self.model))

    self.outputnorm = self.OutputNorm(self.model.critic_output_layers)
    self.outputnorm_target = self.OutputNorm(self.model_target.critic_output_layers)

    self.optimizer = torch.optim.Adam(self.model.parameters(), lr=self.lr)
    self.memory = TrajMemory(self.memory_size, self.batchsize, device, self.history_length)

    self.is_training = False

  def act(self, h, obs, r, done, info, train=False):
    stats = []
    action, h_next, _ = self.model.act(h, obs, r, done, info, train)

    if train:
      self.memory.append(np.float32(r), np.float32(done), info, obs, h_next, action)
      total_updates_target = (len(self.memory) - self.start_training) * self.training_steps
      for self.total_updates in range(self.total_updates, int(total_updates_target) + 1):
        stats += self.train(),
    return action, h_next, stats

  def train(self):
    m, h, a, r, terminals = self.memory.sample()
    terminals = terminals[:, None]  # expand for correct broadcasting below

    h = h[0]  # we recompute all the following memory states
    loss_actor = 0
    loss_critic = 0

    for i in range(self.history_length):
      new_action_distribution, h, _, critic_logits = self.model(h, m[i])
      new_actions = new_action_distribution.rsample()
      new_actions_log_prob = new_action_distribution.log_prob(new_actions)[:, None]

      # critic loss
      _, _, next_value_target, _ = self.model_target((m[i+1], new_actions.detach()))
      next_value_target = reduce(torch.min, next_value_target)

      value_target = self.discount * self.outputnorm_target.unnormalize(next_value_target)
      value_target += self.reward_scale * r[i]
      value_target -= self.entropy_scale * new_actions_log_prob.detach()
      value_target = self.outputnorm.update(value_target)
      values = tuple(c(h) for c, h in zip(self.model.critic_output_layers, critic_logits))  # recompute values (weights changed)

      assert values[0].shape == value_target.shape and not value_target.requires_grad
      loss_critic += sum(mse_loss(v, value_target) for v in values)

      # actor loss
      _, next_value, _ = self.model_nograd((h, m[i+1], new_actions))
      next_value = reduce(torch.min, next_value)
      loss_actor_i = - (1. - terminals) * self.discount * self.outputnorm.unnormalize(next_value)
      loss_actor_i += self.entropy_scale * new_actions_log_prob
      assert loss_actor_i.shape == (self.batchsize, 1)
      loss_actor += self.outputnorm.normalize(loss_actor_i).mean()

    # update model
    self.optimizer.zero_grad()
    loss_total = self.loss_alpha * loss_actor + (1 - self.loss_alpha) * loss_critic
    loss_total.backward()
    self.optimizer.step()

    # update target model and normalizers
    exponential_moving_average(self.model_target.parameters(), self.model.parameters(), self.target_update)
    exponential_moving_average(self.outputnorm_target.parameters(), self.outputnorm.parameters(), self.target_update)

    return dict(
      loss_total=loss_total.detach(),
      loss_critic=loss_critic.detach(),
      loss_actor=loss_actor.detach(),
      outputnorm_mean=float(self.outputnorm.mean),
      outputnorm_std=float(self.outputnorm.std),
      memory_size=len(self.memory),
      # entropy_scale=self.entropy_scale
    )


AvenueAgent = partial(
  Agent,
  entropy_scale=0.05,
  lr=0.0002,
  memory_size=500000,
  batchsize=100,
  training_interval=4,
  start_training=10000,
  Model=partial(ConvDouble)
)


if __name__ == "__main__":
  from agents import Training, run
  from agents import rtac_models
  Rtac_Test = partial(
    Training,
    epochs=3,
    rounds=5,
    steps=500,
    Agent=partial(Agent, device='cpu', memory_size=1000000, start_training=256, batchsize=4),
    # Env=partial(id="Pendulum-v0", real_time=True),
    Env=partial(id="HalfCheetah-v2", real_time=True),
  )

  Rtac_Avenue_Test = partial(
    Training,
    epochs=3,
    rounds=5,
    steps=300,
    Agent=partial(AvenueAgent, device='cpu', start_training=256, batchsize=4, Model=rtac_models.ConvSeparate),
    Env=partial(AvenueEnv, real_time=True),
    Test=partial(number=0),  # laptop can't handle more than that
  )

  run(Rtac_Test)
  # run(Rtac_Avenue_Test)
