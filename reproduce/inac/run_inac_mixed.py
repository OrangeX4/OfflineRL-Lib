import torch
import wandb
from tqdm import trange
from UtilsRL.exp import parse_args, setup
from UtilsRL.logger import CompositeLogger

from offlinerllib.buffer import D4RLTransitionBuffer
from offlinerllib.module.actor import ClippedGaussianActor
from offlinerllib.module.critic import Critic
from offlinerllib.module.net.mlp import MLP
from offlinerllib.policy.model_free import InACPolicy
from offlinerllib.env.mixed import get_mixed_d4rl_mujoco_datasets
from offlinerllib.utils.eval import eval_offline_policy

args = parse_args()
args.task = "-".join(["mixed", args.agent, args.quality1, args.quality2, str(args.ratio)])
exp_name = "_".join([args.task, "seed"+str(args.seed)]) 
logger = CompositeLogger(log_path=f"./log/inac/{args.name}", name=exp_name, loggers_config={
    "FileLogger": {"activate": not args.debug}, 
    "TensorboardLogger": {"activate": not args.debug}, 
    "WandbLogger": {"activate": not args.debug, "config": args, "settings": wandb.Settings(_disable_stats=True), **args.wandb}
})
setup(args, logger)

env, dataset = get_mixed_d4rl_mujoco_datasets(
    agent=args.agent, 
    quality1=args.quality1, 
    quality2=args.quality2, 
    N=args.num_data, 
    ratio=args.ratio, 
    keep_traj=args.keep_traj, 
    normalize_obs=args.normalize_obs, 
    normalize_reward=args.normalize_reward
)
obs_shape = env.observation_space.shape[0]
action_shape = env.action_space.shape[-1]

offline_buffer = D4RLTransitionBuffer(dataset)

actor = ClippedGaussianActor(
    backend=torch.nn.Identity(), 
    input_dim=obs_shape, 
    output_dim=action_shape, 
    reparameterize=True, 
    conditioned_logstd=False, 
    logstd_min=-6, 
    logstd_max=0,
    hidden_dims=args.hidden_dims, 
    device=args.device
).to(args.device)
behavior = ClippedGaussianActor(
    backend=torch.nn.Identity(), 
    input_dim=obs_shape, 
    output_dim=action_shape, 
    reparameterize=True, 
    conditioned_logstd=False, 
    logstd_min=-6, 
    logstd_max=0, 
    hidden_dims=args.hidden_dims, 
    device=args.device
).to(args.device)

critic_q = Critic(
    backend=torch.nn.Identity(), 
    input_dim=obs_shape+action_shape, 
    hidden_dims=args.hidden_dims, 
    ensemble_size=2, 
    device=args.device
).to(args.device)

critic_v = Critic(
    backend=torch.nn.Identity(), 
    input_dim=obs_shape, 
    hidden_dims=args.hidden_dims, 
    device=args.device
).to(args.device)

policy = InACPolicy(
    actor=actor, behavior=behavior, critic_q=critic_q, critic_v=critic_v, 
    temperature=args.temperature, 
    discount=args.discount, 
    tau=args.tau, 
    device=args.device
).to(args.device)
policy.configure_optimizers(
    actor_lr=args.learning_rate, 
    critic_q_lr=args.learning_rate, 
    critic_v_lr=args.learning_rate, 
    behavior_lr=args.learning_rate
)

# main loop
policy.train()
for i_epoch in trange(1, args.max_epoch+1):
    for i_step in range(args.step_per_epoch):
        batch = offline_buffer.random_batch(args.batch_size)
        train_metrics = policy.update(batch)
    if i_epoch % args.eval_interval == 0:
        eval_metrics = eval_offline_policy(env, policy, args.eval_episode, seed=args.seed)
        logger.info(f"Episode {i_epoch}: \n{eval_metrics}")
    if i_epoch % args.log_interval == 0:
        logger.log_scalars("", train_metrics, step=i_epoch)
        logger.log_scalars("Eval", eval_metrics, step=i_epoch)
    if i_epoch % args.save_interval == 0:
        logger.log_object(name=f"policy_{i_epoch}.pt", object=policy.state_dict(), path=f"./out/inac/{args.name}/{args.task}/seed{args.seed}/policy/")

