import os
from abc import abstractmethod
import torch
import torch.utils.data.dataset as dataset
import json
import numpy as np
from PIL import Image
from scipy.io import loadmat
import h5py
import re



concat_pad = lambda x, padding_len: torch.cat([x, torch.zeros(([padding_len] + list(x.shape[1:])), dtype = x.dtype)], dim = 0)

class BaseDataset(dataset.Dataset):
    tasks = ['2x_5', '2x_10', '2x_15', '4x_5', '4x_10', '4x_15', '8x_5', '8x_10', '8x_15']
    task_tokenizer = {task: i for i, task in enumerate(tasks)}
    
    def __init__(self, block_size, rtg_scale, data_dir, action_dim) -> None:
        super(BaseDataset, self).__init__()
        self.block_size = block_size
        self.rtg_scale = rtg_scale
        self.data_dir = data_dir
        self.action_dim = action_dim

    def __len__(self):
        return len(os.listdir(self.data_dir))
    
    @abstractmethod
    def __getitem__(self, index):
        pass


def extract_task(s):
    pattern = r'\d+_\d+'
    match = re.search(pattern, s)
    return match.group()
    

class TrainingDataset(BaseDataset):

    def __init__(self, block_size, rtg_scale, data_dir, action_dim, state_file_path) -> None:
        super().__init__(block_size, rtg_scale, data_dir, action_dim)
        self.state_file_path = state_file_path
        self.timestep_max = 30   
    
    def _get_image(self, image_type, index, trajectory):
        with h5py.File(self.state_file_path, 'r') as file:
            data = file[f'/CSMRI/csrmi_{image_type}_image_{index}_trajectory_{trajectory}.png'][:]
        image = torch.from_numpy(data)
        return image

    def _get_states(self, index, traj_start, traj_end, pad = None):
        state_tensors = []

        for trajectory in range(traj_start, traj_end):
            x = self._get_image('x', index, trajectory)
            x = x/255
            state_tensors.append(x)

        states = torch.stack(state_tensors)

        if pad is not None:
            states = torch.cat([states, torch.zeros(([pad] + list(states.shape[1:])), dtype = x.dtype)])

        seq_len = states.shape[0]
        states = states.view(seq_len, -1)

        return states
    
    @staticmethod
    def _get_actions(action_dict, traj_start, traj_end, pad = None):
        action_lis = [torch.Tensor(action_dict[key][traj_start:traj_end]) for key in action_dict.keys()]
              
        actions = torch.stack((action_lis), dim = 1)
        
        if pad is not None:
            padding = torch.zeros((pad, actions.shape[1]))
            actions = torch.cat([actions, padding], dim = 0)

        return actions

    def __getitem__(self, 
                    index: int
                    ) -> torch.Tensor:
        #TODO tokenizer for the task
        block_size = self.block_size
        traj_name = os.listdir(self.data_dir)[index]
        traj_path = os.path.join(self.data_dir, traj_name)
        file_index = int(traj_name.split('_')[1].split('.')[0])
        
        with open(traj_path, 'r') as file:
            traj_dict = json.load(file)
            
        traj_len = len(traj_dict['RTG'])
        
        acceleration = traj_dict['acceleration']
        noise_level = traj_dict['noise_level']
        
        task = acceleration[0] + '_' + noise_level
        
        encode = lambda s: self.task_tokenizer[s]
        
        task = encode(task)
        task = torch.tensor([task])
        task = task.repeat(block_size)

        if traj_len >= block_size:
            if traj_len==block_size:
                start = 0
            else:
                start = np.random.randint(0, traj_len - block_size)

            actions = self._get_actions(traj_dict['Actions'], start, start + block_size)
            rtg = np.array(traj_dict['RTG'][start:start+block_size])
            rtg = rtg/self.rtg_scale
            rtg = torch.from_numpy(rtg).type(torch.float32).reshape(-1, 1)
            timesteps = torch.arange(start, start + block_size, dtype = torch.float32).reshape(-1, 1)
            states = self._get_states(file_index, start, start + block_size)
            traj_masks = torch.ones(block_size)
        else:
            padding_len = block_size - traj_len
            concat_pad = lambda x, padding_len: torch.cat([x, 
                                              torch.zeros(([padding_len] + 
                                                           list(x.shape[1:])),
                                              dtype = x.dtype)], dim = 0)
            actions = self._get_actions(traj_dict['Actions'], 0, traj_len, pad = padding_len)  
            rtg = np.array(traj_dict['RTG'])/self.rtg_scale
            rtg = torch.from_numpy(rtg).type(torch.float32).reshape(-1, 1)
            rtg = concat_pad(rtg, padding_len)
            traj_masks = torch.cat([torch.ones(traj_len),torch.zeros(padding_len)], dim = 0)
            states = self._get_states(file_index, 0, traj_len, pad = padding_len)
            timesteps = torch.arange(start = 0, end = block_size).reshape(-1, 1)
        
        traj_masks = traj_masks.unsqueeze(dim = -1)
        #timesteps = timesteps/self.timestep_max
        return states, actions, rtg, traj_masks, timesteps, task


class EvaluationDataset(BaseDataset):

    def __init__(self, block_size, rtg_scale, data_dir, action_dim, rtg_target) -> None:
        super().__init__(block_size, rtg_scale, data_dir, action_dim)
        self.rtg_target = rtg_target
        self.fns = [im for im in os.listdir(self.data_dir) if im.endswith('.mat')]
        self.fns.sort()
    
    def __getitem__(self, index):
        fn = self.fns[index]
        task_str = extract_task(fn)
        task = task_str[0] + 'x' + task_str[1:]
        encode = lambda s: self.task_tokenizer[s]
        task = encode(task)
        task = torch.tensor([task])
        
        mat = loadmat(os.path.join(self.data_dir, fn))
        action_dict = {}
        action_dict['x0'] = mat['x0']
        action_dict['y0'] = mat['y0']
        action_dict['mask'] = mat['mask']
        action_dict['ATy0'] = mat['ATy0']
        action_dict['gt'] = mat['gt'] 
        action_dict['x0'] = np.clip(action_dict['x0'], a_min=0, a_max = None)
        

        x = mat['x0'][..., 0].reshape(1, 128, 128)
        states = x.reshape(1, -1)
        rtg = self.rtg_target/self.rtg_scale
        rtg = torch.Tensor([rtg]).reshape(1, 1)
        actions = torch.zeros((self.action_dim))
        return (states, rtg, actions, task), action_dict
    
    def get_eval_obs(self, index):
        policy_inputs, mat = self.__getitem__(index)
        states, rtg, actions = policy_inputs
        states = states.unsqueeze(dim = 0)
        rtg = rtg.unsqueeze(dim = 0)
        actions = actions.unsqueeze(dim = 0)
        return (states, rtg, actions), mat

        




        

        

        

        
        