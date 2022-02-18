from argparse import ArgumentParser
from dfno import create_standard_partitions, DistributedFNONd
from distdl.utilities.torch import *
from distdl.utilities.tensor_decomposition import *
from memory_profiler import profile
from pathlib import Path

import distdl.nn as dnn
import gc
import numpy as np
import os
import time
import torch

def compute_distribution_info(P, shape):
    info = {}
    ts = TensorStructure()
    ts.shape = shape
    info['shapes'] = compute_subtensor_shapes_balanced(ts, P.shape)
    info['starts'] = compute_subtensor_start_indices(info['shapes'])
    info['stops']  = compute_subtensor_stop_indices(info['shapes'])
    info['index']  = tuple(P.index)
    info['shape']  = info['shapes'][info['index']]
    info['start']  = info['starts'][info['index']]
    info['stop']   = info['stops'][info['index']]
    info['slice']  = assemble_slices(info['start'], info['stop'])
    return info

parser = ArgumentParser()
parser.add_argument('--input-shape', '-is', type=int, nargs='+')
parser.add_argument('--partition_shape', '-ps', type=int, nargs='+')
parser.add_argument('--width', '-w', type=int, default=20)
parser.add_argument('--modes', '-m', type=int, nargs='+')
parser.add_argument('--num-timesteps', '-nt', type=int, default=10)
parser.add_argument('--device', '-d', type=str, default='cpu')
parser.add_argument('--num-gpus', '-ngpu', type=int, default=0)
parser.add_argument('--benchmark-type', '-bt', type=str, default='eval')
parser.add_argument('--profiler-output', '-po', type=Path, default=Path('memory_profiler.log'))

args = parser.parse_args()
input_shape = args.input_shape
partition_shape = args.partition_shape
width = args.width
modes = args.modes
nt = args.num_timesteps
ngpu = args.num_gpus
benchmark_type = args.benchmark_type
profiler_output = args.profiler_output

P_world, P_x, P_0 = create_standard_partitions(partition_shape)
device = torch.device('cpu') if args.device == 'cpu' else torch.device(f'cuda:{P_x.rank % ngpu}')
profiler_output = Path(f'{profiler_output.stem}_{P_x.rank:04d}{profiler_output.suffix}')

x_shape = input_shape
y_shape = (*input_shape[:-1], nt)
x_info = compute_distribution_info(P_x, x_shape)
y_info = compute_distribution_info(P_x, y_shape)

criterion = dnn.DistributedMSELoss(P_x).to(device)
network = DistributedFNONd(P_x, width, modes, nt, device='cpu')
network.eval()

dummy = torch.rand(size=tuple(x_info['shape']), device=torch.device('cpu'), dtype=torch.float32)
y = network(dummy)
del dummy
del y
gc.collect()

network.to(device)
P_x._comm.Barrier()
x = torch.rand(size=tuple(x_info['shape']), device=device, dtype=torch.float32)

f = open(profiler_output, 'w+')
@profile(stream=f)
def bench():
    if benchmark_type == 'eval':
        with torch.no_grad():
            network.eval()
            P_x._comm.Barrier()
            t0 = time.time()
            y = network(x)
            t1 = time.time()
            print(f'rank = {P_x.rank}, dt = {t1-t0}')

bench()
f.close()
