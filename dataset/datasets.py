import os
from abc import abstractmethod
import torch
import torch.utils.data.dataset as dataset
import json
import numpy as np
from PIL import Image
from scipy.io import loadmat
import h5py


#TRAINING_DIR = os.path.join(os.getcwd(), 'dataset/data/Images_128')
#TRAINING_DICTIONARY_DIR = ''
#STATE_DIR = ''
#EVALUATION_DIR = ''

concat_pad = lambda x, padding_len: torch.cat([x, torch.zeros(([padding_len] + list(x.shape[1:])), dtype = x.dtype)], dim = 0)

class BaseDataset(dataset.Dataset):
    def __init__(self, block_size, rtg_scale, data_dir, action_dim) -> None:
        super(BaseDataset, self).__init__()
        self.block_size = block_size
        self.rtg_scale = rtg_scale
        self.data_dir = data_dir
        self.action_dim = action_dim

    def __len__(self):
        return len(os.listdir(self._training_dir))
    
    @abstractmethod
    def __getitem__(self, index):
        pass




class TrainingDataset(BaseDataset):
    parameters = ['sigma_d', 'mu', 'T']

    def __init__(self, block_size, rtg_scale, data_dir, action_dim, state_file_path) -> None:
        super().__init__(block_size, data_dir, rtg_scale, action_dim)
        self.state_file_path = state_file_path
        self.timestep_max = 30   
    
    def _get_image(self, image_type, index, trajectory):
        with h5py.File(self.state_file_path, 'r') as file:
            data = file[f'CSMRI/csmri_{image_type}_image_{index}_trajectory_{trajectory}'][:]

        image = torch.from_numpy(data)
        return image


    def _get_states(self, index, traj_start, traj_end, pad = None):
        state_tensors = []

        for trajectory in range(traj_start, traj_end):
            x = self._get_image('x', index, trajectory)
            z = self._get_image('z', index, trajectory)
            u = self._get_image('u', index, trajectory)

            x = x/255
            z = z/255
            u = u/255

            variable = torch.stack([x, z, u])
            state_tensors.append(variable)

        states = torch.stack(state_tensors)

        if pad is not None:
            states = torch.cat([states, torch.zeros(([pad] + list(x.shape[1:])), dtype = x.dtype)])

        seq_len = states.shape[0]
        states = states.view(seq_len, -1)

        return states
    
    @staticmethod
    def _get_actions(action_dict, traj_start, traj_end, pad = None):
        action_lis = [torch.Tensor(action_dict[key][traj_start: traj_end]) for key in action_dict.keys()]
        actions = torch.stack((action_lis), dim = 1)
        
        if pad is not None:
            padding = torch.zeros((pad, actions.shape[1]))
            actions = torch.cat([actions, padding], dim = 0)

        return actions
        


    def __getitem__(self, 
                    index: int
                    ) -> torch.Tensor:
        #TODO tokenizer for the task
        block_size = self.block_size//3
        traj_name = self._training_dictionary_dir[index]
        traj_path = os.path.join(self._training_dictionary_dir, traj_name)
        file_index = int(traj_name.split('_')[1].split('.')[0])
        
        with open(traj_path, 'r') as file:
            traj_dict = json.load(file)
        
        traj_len = len(traj_dict['state_path'])
        traj_dict['Actions']['T'] = [value if index % 5 == 4 else 0 for index, value in enumerate(traj_dict['Actions']['T'])]

        if traj_len >= block_size:
            start = np.random.randint(0, traj_len - block_size)

            actions = self._get_actions(traj_dict['actions'], start, start + block_size)
            rtg = traj_dict['rtg'][start: start + block_size]
            rtg = rtg/self.rtg_scale
            rtg = torch.from_numpy(rtg).reshape(-1, 1)
            timesteps = torch.arange(start, start + block_size)
            states = self._get_states(file_index, start, start + block_size)
            traj_masks = torch.ones(block_size)
        else:
            padding_len = block_size - traj_len
            concat_pad = lambda x, padding_len: torch.cat([x, 
                                              torch.zeros(([padding_len] + 
                                                           list(x.shapes[1:])),
                                              dtype = x.dtype)], dim = 0)
            actions = self._get_actions(traj_dict['actions'], 0, traj_len, pad = padding_len)
            actions = concat_pad(actions, padding_len)
            rtg = traj_dict['rtg']/self.rtg_scale
            rtg = torch.from_numpy(rtg).reshape(-1, 1)
            rtg = concat_pad(rtg, padding_len)
            traj_masks = torch.cat([torch.ones(traj_len),
                                    torch.zeros(padding_len)],
                                    dim = 0)
            states = self._get_states(file_index, 0, actions.shape[0], pad = padding_len)
            timesteps = torch.arange(start = 0, end = block_size)
        
        #timesteps = timesteps/self.timestep_max
        return states, actions, rtg, traj_masks, timesteps


class EvaluationDataset(BaseDataset):

    def __init__(self, block_size, rtg_scale, data_dir, action_dim, rtg_target) -> None:
        super().__init__(block_size, rtg_scale, data_dir, action_dim)
        self.rtg_target = rtg_target
        self.fns = [im for im in os.listdir(self.data_dir) if im.endswith('.mat')]
        self.fns.sort()



    def __len__(self):
        return len(os.listdir(self.datadir))
    
    def __getitem__(self, index):
        block_size = self.block_size//3
        fn = self.fns[index]
        mat = loadmat(os.path.join(self.data_dir, fn))

        x = mat['x0'][..., 0].reshape(1, 128, 128)
        x = torch.from_numpy(x)
        z = x.clone().detach()
        u = torch.zeros_like(x)
        states = torch.cat([x, z, u], dim = 0).view(1, -1)
        rtg = self.rtg_target/self.rtg_scale
        rtg = torch.Tensor([rtg]).reshape(1, 1)
        actions = torch.zeros((self.action_dim))
        return (states, rtg, actions), mat
    
    def get_eval_obs(self, index):
        policy_inputs, mat = self.__getitem__(index)
        states, rtg, actions = policy_inputs
        states = states.unsqueeze(dim = 0)
        rtg = rtg.unsqueeze(dim = 0)
        actions = actions.unsqueeze(dim = 0)
        return (states, rtg, actions), mat

        




        

        

        

        
        