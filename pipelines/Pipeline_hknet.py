"""
This file contains the class Pipeline_hknet, 
which is used to train and test the hyper-KalmanNet model.

functions:
* NNTrain: train the hyper-KalmanNet model on one dataset
* NNTest: test the hyper-KalmanNet model on one dataset
* NNTrain_mixdatasets: train the hyper-KalmanNet model on multiple datasets
* NNTest_alldatasets: test the hyper-KalmanNet model on multiple datasets
"""

import torch
import torch.nn as nn
import random
import time
import math

class Pipeline_hknet:

    def __init__(self, Time, folderName, modelName):
        super().__init__()
        self.Time = Time
        self.folderName = folderName + '/'
        self.modelName = modelName
        self.modelFileName = self.folderName + "model_" + self.modelName + ".pt"
        self.PipelineName = self.folderName + "pipeline_" + self.modelName + ".pt"  

    def save(self):
        torch.save(self, self.PipelineName)

    def print_grad(self, grad):
        print('Gradient:', grad)

    def setModel(self, hnet, mnet):
        self.hnet = hnet
        self.mnet = mnet

    def setTrainingParams(self, args):
        self.args = args
        if args.use_cuda:
            self.device = torch.device('cuda')
        else:
            self.device = torch.device('cpu')
        self.N_steps = args.n_steps  # Number of Training Steps
        self.N_B = args.n_batch # Number of Samples in Batch
        self.learningRate = args.lr # Learning Rate
        self.weightDecay = args.wd # L2 Weight Regularization - Weight Decay
        self.alpha = args.alpha # Composition loss factor
        # MSE LOSS Function
        self.loss_fn = nn.MSELoss(reduction='mean')

        # Optimize hnet and mnet in an end-to-end fashion
        self.optimizer = torch.optim.Adam(self.hnet.parameters(), \
            lr=self.learningRate, weight_decay=self.weightDecay)

    def NNTrain_mixdatasets(self, SoW_train_range, sys_model, cv_input_tuple, cv_target_tuple, train_input_tuple, train_target_tuple, path_results, \
        cv_init, train_init, MaskOnState=False, train_lengthMask=None,cv_lengthMask=None):
        
        if self.args.wandb_switch: 
            import wandb

        self.MSE_cv_dB_opt = 1000
        self.MSE_cv_idx_opt = 0

        # Init MSE Loss
        self.MSE_cv_linear_epoch = torch.zeros([self.N_steps])
        self.MSE_cv_dB_epoch = torch.zeros([self.N_steps])
        self.MSE_train_linear_epoch = torch.zeros([self.N_steps])
        self.MSE_train_dB_epoch = torch.zeros([self.N_steps])

        # dataset size
        for i in SoW_train_range[:-1]:# except the last one
            assert(train_target_tuple[i][0].shape[1]==train_target_tuple[i+1][0].shape[1])
            assert(train_input_tuple[i][0].shape[1]==train_input_tuple[i+1][0].shape[1])
            assert(train_input_tuple[i][0].shape[2]==train_input_tuple[i+1][0].shape[2])
            # check all datasets have the same m, n, T   
        sysmdl_m = train_target_tuple[0][0].shape[1] # state x dimension
        sysmdl_n = train_input_tuple[0][0].shape[1] # input y dimension
        sysmdl_T = train_input_tuple[0][0].shape[2] # sequence length 
        
        for ti in range(0, self.N_steps):
            # each turn, go through all datasets
            #################
            ### Training  ###
            #################          
            # Training Mode
            self.hnet.train()
            self.mnet.train()
            self.mnet.batch_size = self.N_B       
            MSE_trainbatch_linear_LOSS_total = 0 # total train loss for all datasets
           
            for i in SoW_train_range: # dataset i 
                self.optimizer.zero_grad()  
                 # Init Training Batch tensors
                y_training_batch = torch.zeros([self.N_B, sysmdl_n, sysmdl_T]).to(self.device)
                train_target_batch = torch.zeros([self.N_B, sysmdl_m, sysmdl_T]).to(self.device)
                x_out_training_batch = torch.zeros([self.N_B, sysmdl_m, sysmdl_T]).to(self.device)
                if self.args.randomLength:
                    MSE_train_linear_LOSS = torch.zeros([self.N_B])
                # Init Sequence
                train_init_batch = torch.empty([self.N_B, sysmdl_m,1]).to(self.device)
                # Init Hidden State
                self.hnet.init_hidden()
                self.mnet.init_hidden()  
                # SoW: make sure SoWs are consistent
                assert torch.allclose(cv_input_tuple[i][1], cv_target_tuple[i][1]) 
                assert torch.allclose(train_input_tuple[i][1], train_target_tuple[i][1]) 
                self.mnet.UpdateSystemDynamics(sys_model[i])
                # req grad
                train_input_tuple[i][1].requires_grad = True # SoW_train
                train_input_tuple[i][0].requires_grad = True # input y
                train_target_tuple[i][0].requires_grad = True # target x
                train_init[i].requires_grad = True # init x0
                # data size
                self.N_E = len(train_input_tuple[i][0]) # Number of Training Sequences
                # mask on state
                if MaskOnState:
                    mask = torch.tensor([True,False,False])
                    if sysmdl_m == 2: 
                        mask = torch.tensor([True,False])
                # Randomly select N_B training sequences
                assert self.N_B <= self.N_E # N_B must be smaller than N_E
                n_e = random.sample(range(self.N_E), k=self.N_B)
                dataset_index = 0
                for index in n_e:
                    # Training Batch
                    if self.args.randomLength:
                        y_training_batch[dataset_index,:,train_lengthMask[i][index,:]] = train_input_tuple[i][0][index,:,train_lengthMask[index,:]]
                        train_target_batch[dataset_index,:,train_lengthMask[i][index,:]] = train_target_tuple[i][0][index,:,train_lengthMask[index,:]]
                    else:
                        y_training_batch[dataset_index,:,:] = train_input_tuple[i][0][index]
                        train_target_batch[dataset_index,:,:] = train_target_tuple[i][0][index]                                 
                    # Init Sequence
                    train_init_batch[dataset_index,:,0] = torch.squeeze(train_init[i][index])                  
                    dataset_index += 1
                self.mnet.InitSequence(train_init_batch, sysmdl_T)
                
                # Forward Computation
                weights = self.hnet(train_input_tuple[i][1])
                for t in range(0, sysmdl_T):
                    x_out_training_batch[:, :, t] = torch.squeeze(self.mnet(torch.unsqueeze(y_training_batch[:, :, t],2), weights=weights))
                
                ### weights.register_hook(self.print_grad)

                # Compute Training Loss
                MSE_trainbatch_linear_LOSS = 0 # loss for each dataset
                if(MaskOnState):
                    if self.args.randomLength:
                        dataset_index = 0
                        for index in n_e:# mask out the padded part when computing loss
                            MSE_train_linear_LOSS[dataset_index] = self.loss_fn(x_out_training_batch[dataset_index,mask,train_lengthMask[i][index]], train_target_batch[dataset_index,mask,train_lengthMask[index]])
                            dataset_index += 1
                        MSE_trainbatch_linear_LOSS = torch.mean(MSE_train_linear_LOSS)
                        MSE_trainbatch_linear_LOSS_total = MSE_trainbatch_linear_LOSS_total + MSE_trainbatch_linear_LOSS
                    else:
                        MSE_trainbatch_linear_LOSS = self.loss_fn(x_out_training_batch[:,mask,:], train_target_batch[:,mask,:])
                        MSE_trainbatch_linear_LOSS_total = MSE_trainbatch_linear_LOSS_total + MSE_trainbatch_linear_LOSS
                else: # no mask on state
                    if self.args.randomLength:
                        dataset_index = 0
                        for index in n_e:# mask out the padded part when computing loss
                            MSE_train_linear_LOSS[dataset_index] = self.loss_fn(x_out_training_batch[dataset_index,:,train_lengthMask[i][index]], train_target_batch[dataset_index,:,train_lengthMask[index]])
                            dataset_index += 1
                        MSE_trainbatch_linear_LOSS = torch.mean(MSE_train_linear_LOSS)
                        MSE_trainbatch_linear_LOSS_total = MSE_trainbatch_linear_LOSS_total + MSE_trainbatch_linear_LOSS
                    else: 
                        MSE_trainbatch_linear_LOSS = self.loss_fn(x_out_training_batch, train_target_batch)
                        MSE_trainbatch_linear_LOSS_total = MSE_trainbatch_linear_LOSS_total + MSE_trainbatch_linear_LOSS
                
                ##################
                ### Optimizing ###
                ##################
                # FIXME: Can only optimize for each dataset one by one, since joint optimization cause in-place operation error
                MSE_trainbatch_linear_LOSS.backward(retain_graph=True)
                self.optimizer.step()

            # averaged dB Loss
            MSE_trainbatch_linear_LOSS_average = MSE_trainbatch_linear_LOSS_total / len(SoW_train_range)
            self.MSE_train_linear_epoch[ti] = MSE_trainbatch_linear_LOSS_average.item()
            self.MSE_train_dB_epoch[ti] = 10 * torch.log10(self.MSE_train_linear_epoch[ti])

            ##################
            ### Validation ###
            ##################
            MSE_cvbatch_linear_LOSS = 0
            # Cross Validation Mode
            self.hnet.eval()
            self.mnet.eval()

            # data size
            self.N_CV = len(cv_input_tuple[i][0])
            sysmdl_T_test = cv_input_tuple[i][0].shape[2] 
            if self.args.randomLength:
                MSE_cv_linear_LOSS = torch.zeros([self.N_CV*len(SoW_train_range)])
            # Init Output
            x_out_cv_batch = torch.empty([self.N_CV*len(SoW_train_range), sysmdl_m, sysmdl_T_test]).to(self.device)                   
            # Update Batch Size for mnet
            self.mnet.batch_size = self.N_CV 

            with torch.no_grad():
                for i in SoW_train_range: # dataset i 
                    # Init Hidden State
                    self.hnet.init_hidden()
                    self.mnet.init_hidden()
                    # Init Sequence                    
                    self.mnet.InitSequence(cv_init[i], sysmdl_T_test)                       
                    
                    weights = self.hnet(cv_input_tuple[i][1])
                    for t in range(0, sysmdl_T_test):
                        x_out_cv_batch[self.N_CV*i:self.N_CV*(i+1), :, t] = torch.squeeze(self.mnet(torch.unsqueeze(cv_input_tuple[i][0][:, :, t],2), weights=weights))
                    
                    # Compute CV Loss
                    MSE_cvbatch_linear_LOSS_i = MSE_cvbatch_linear_LOSS
                    if(MaskOnState):
                        if self.args.randomLength:
                            for index in range(self.N_CV):
                                MSE_cv_linear_LOSS[index+self.N_CV*i] = self.loss_fn(x_out_cv_batch[index+self.N_CV*i,mask,cv_lengthMask[i][index]], cv_target_tuple[i][0][index,mask,cv_lengthMask[index]])
                            MSE_cvbatch_linear_LOSS = MSE_cvbatch_linear_LOSS + torch.mean(MSE_cv_linear_LOSS[self.N_CV*i:self.N_CV*(i+1)])
                        else:          
                            MSE_cvbatch_linear_LOSS = MSE_cvbatch_linear_LOSS + self.loss_fn(x_out_cv_batch[self.N_CV*i:self.N_CV*(i+1),mask,:], cv_target_tuple[i][0][:,mask,:])
                    else:
                        if self.args.randomLength:
                            for index in range(self.N_CV):
                                MSE_cv_linear_LOSS[index+self.N_CV*i] = self.loss_fn(x_out_cv_batch[index+self.N_CV*i,:,cv_lengthMask[i][index]], cv_target_tuple[i][0][index,:,cv_lengthMask[index]])
                            MSE_cvbatch_linear_LOSS = MSE_cvbatch_linear_LOSS + torch.mean(MSE_cv_linear_LOSS[self.N_CV*i:self.N_CV*(i+1)])
                        else:
                            MSE_cvbatch_linear_LOSS = MSE_cvbatch_linear_LOSS + self.loss_fn(x_out_cv_batch[self.N_CV*i:self.N_CV*(i+1)], cv_target_tuple[i][0])
                    
                    # Print loss for each dataset
                    MSE_cvbatch_linear_LOSS_i = MSE_cvbatch_linear_LOSS - MSE_cvbatch_linear_LOSS_i
                    MSE_cvbatch_dB_LOSS_i = 10 * math.log10(MSE_cvbatch_linear_LOSS_i.item())
                    print(f"MSE Validation on dataset {i}:", MSE_cvbatch_dB_LOSS_i,"[dB]")
                
                # averaged dB Loss
                MSE_cvbatch_linear_LOSS = MSE_cvbatch_linear_LOSS / len(SoW_train_range)
                self.MSE_cv_linear_epoch[ti] = MSE_cvbatch_linear_LOSS.item()
                self.MSE_cv_dB_epoch[ti] = 10 * torch.log10(self.MSE_cv_linear_epoch[ti])
                # save model with best averaged loss on all datasets
                if (self.MSE_cv_dB_epoch[ti] < self.MSE_cv_dB_opt):
                    self.MSE_cv_dB_opt = self.MSE_cv_dB_epoch[ti]
                    self.MSE_cv_idx_opt = ti
                    
                    torch.save(self.hnet, path_results + 'hnet_best-model.pt')
                    torch.save(self.mnet, path_results + 'mnet_best-model.pt')

            ########################
            ### Training Summary ###
            ########################
            print(ti, "MSE Training Average:", self.MSE_train_dB_epoch[ti], "[dB]", "MSE Validation Average:", self.MSE_cv_dB_epoch[ti],
                "[dB]")
                    
            if (ti > 1):
                d_train = self.MSE_train_dB_epoch[ti] - self.MSE_train_dB_epoch[ti - 1]
                d_cv = self.MSE_cv_dB_epoch[ti] - self.MSE_cv_dB_epoch[ti - 1]
                print("diff MSE Training :", d_train, "[dB]", "diff MSE Validation :", d_cv, "[dB]")

            print("Optimal idx:", self.MSE_cv_idx_opt, "Optimal :", self.MSE_cv_dB_opt, "[dB]")
            
            ### Optinal: record loss on wandb
            if self.args.wandb_switch:
                wandb.log({
                    "train_loss": self.MSE_train_dB_epoch[ti],
                    "val_loss": self.MSE_cv_dB_epoch[ti]})
            ###
        return [self.MSE_cv_linear_epoch, self.MSE_cv_dB_epoch, self.MSE_train_linear_epoch, self.MSE_train_dB_epoch]

    def NNTest_alldatasets(self, SoW_test_range, sys_model, test_input_tuple, test_target_tuple, path_results,test_init,\
        MaskOnState=False,load_model=False,load_model_path=None, test_lengthMask=None):
        if self.args.wandb_switch: 
            import wandb
        # Load model
        if load_model:
            self.hnet = torch.load(load_model_path[0], map_location=self.device)
            self.mnet = torch.load(load_model_path[1], map_location=self.device) 
        else:
            self.hnet = torch.load(path_results+'hnet_best-model.pt', map_location=self.device)
            self.mnet = torch.load(path_results+'mnet_best-model.pt', map_location=self.device) 
        
        # dataset size    
        for i in SoW_test_range[:-1]:# except the last one
            assert(test_target_tuple[i][0].shape[1]==test_target_tuple[i+1][0].shape[1])
            assert(test_target_tuple[i][0].shape[2]==test_target_tuple[i+1][0].shape[2])
            # check all datasets have the same m, T   
        sysmdl_m = test_target_tuple[0][0].shape[1]
        sysmdl_T_test = test_target_tuple[0][0].shape[2]
        total_size = 0 # total size for all datasets
        for i in SoW_test_range: 
            total_size += test_input_tuple[i][0].shape[0] 
        self.MSE_test_linear_arr = torch.zeros([total_size])
        x_out_test = torch.zeros([total_size, sysmdl_m,sysmdl_T_test]).to(self.device)
        current_idx = 0

        for i in SoW_test_range: # dataset i   
            # SoW
            assert torch.allclose(test_input_tuple[i][1], test_target_tuple[i][1]) 
            SoW_test = test_input_tuple[i][1]
            self.mnet.UpdateSystemDynamics(sys_model[i])
            # load data
            test_input = test_input_tuple[i][0]
            test_target = test_target_tuple[i][0]
            # test data size
            self.N_T = test_input.shape[0]  
            if MaskOnState:
                mask = torch.tensor([True,False,False])
                if sysmdl_m == 2: 
                    mask = torch.tensor([True,False])

            # MSE LOSS Function
            loss_fn = nn.MSELoss(reduction='mean')

            # Test mode
            self.hnet.eval()
            self.mnet.eval()
            self.mnet.batch_size = self.N_T
            # Init Hidden State
            self.hnet.init_hidden()
            self.mnet.init_hidden()

            torch.no_grad()

            start = time.time()

            # Init Sequence
            self.mnet.InitSequence(test_init[i], sysmdl_T_test)               
            
            weights = self.hnet(SoW_test)
            for t in range(0, sysmdl_T_test):
                x_out_test[current_idx:current_idx+self.N_T,:, t] = torch.squeeze(self.mnet(torch.unsqueeze(test_input[:,:, t],2), weights=weights))
            
            end = time.time()
            t = end - start

            # MSE loss
            for j in range(self.N_T):# cannot use batch due to different length and std computation  
                if(MaskOnState):
                    if self.args.randomLength:
                        self.MSE_test_linear_arr[current_idx+j] = loss_fn(x_out_test[current_idx+j,mask,test_lengthMask[j]], test_target[j,mask,test_lengthMask[j]]).item()
                    else:
                        self.MSE_test_linear_arr[current_idx+j] = loss_fn(x_out_test[current_idx+j,mask,:], test_target[j,mask,:]).item()
                else:
                    if self.args.randomLength:
                        self.MSE_test_linear_arr[current_idx+j] = loss_fn(x_out_test[current_idx+j,:,test_lengthMask[j]], test_target[j,:,test_lengthMask[j]]).item()
                    else:
                        self.MSE_test_linear_arr[current_idx+j] = loss_fn(x_out_test[current_idx+j,:,:], test_target[j,:,:]).item()
            
            # Average for dataset i
            MSE_test_linear_avg_dataset_i = torch.mean(self.MSE_test_linear_arr[current_idx:current_idx+self.N_T])
            MSE_test_dB_avg_dataset_i = 10 * torch.log10(MSE_test_linear_avg_dataset_i)

            # Standard deviation for dataset i
            MSE_test_linear_std_dataset_i = torch.std(self.MSE_test_linear_arr[current_idx:current_idx+self.N_T], unbiased=True)

            # Confidence interval for dataset i
            test_std_dB_dataset_i = 10 * torch.log10(MSE_test_linear_std_dataset_i + MSE_test_linear_avg_dataset_i) - MSE_test_dB_avg_dataset_i

            # Print MSE and std for dataset i
            str = self.modelName + "-" + f"dataset {i}" + "-" + "MSE Test:"
            print(str, MSE_test_dB_avg_dataset_i, "[dB]")
            str = self.modelName + "-"  + f"dataset {i}" + "-" + "STD Test:"
            print(str, test_std_dB_dataset_i, "[dB]")
            # Print Run Time
            print("Inference Time:", t)

            ### Optinal: record loss on wandb
            if self.args.wandb_switch:
                wandb.log({f'test_loss for dataset {i}':MSE_test_dB_avg_dataset_i})
            ###

            # update index
            current_idx += self.N_T
        
        # average MSE over all datasets
        self.MSE_test_linear_avg = torch.mean(self.MSE_test_linear_arr)
        self.MSE_test_dB_avg = 10 * torch.log10(self.MSE_test_linear_avg)
        # Average std
        self.MSE_test_linear_std = torch.std(self.MSE_test_linear_arr, unbiased=True)
        self.test_std_dB = 10 * torch.log10(self.MSE_test_linear_std + self.MSE_test_linear_avg) - self.MSE_test_dB_avg
        # Print MSE and std
        str = self.modelName + "-" + "Average" + "-" + "MSE Test:"
        print(str, self.MSE_test_dB_avg, "[dB]")
        str = self.modelName + "-"  + "Average" + "-" + "STD Test:"
        print(str, self.test_std_dB, "[dB]")

        ### Optinal: record loss on wandb
        if self.args.wandb_switch:
            wandb.log({f'averaged test loss':self.MSE_test_dB_avg})
        ###

        return [self.MSE_test_linear_arr, self.MSE_test_linear_avg, self.MSE_test_dB_avg, x_out_test]

    def NNTrain(self, sys_model, cv_input_tuple, cv_target_tuple, train_input_tuple, train_target_tuple, path_results, \
            cv_init, train_init, MaskOnState=False, train_lengthMask=None,cv_lengthMask=None):
            # SoW 
            assert torch.allclose(cv_input_tuple[1], cv_target_tuple[1]) 
            assert torch.allclose(train_input_tuple[1], train_target_tuple[1]) 
            SoW_cv = cv_input_tuple[1]
            SoW_train = train_input_tuple[1]
            SoW_train.requires_grad = True
            self.mnet.UpdateSystemDynamics(sys_model)
            # load data
            cv_input = cv_input_tuple[0]
            cv_target = cv_target_tuple[0]
            train_input = train_input_tuple[0]
            train_input.requires_grad = True
            train_target = train_target_tuple[0]
            train_target.requires_grad = True
            train_init.requires_grad = True
            # data size
            self.N_E = len(train_input)
            self.N_CV = len(cv_input)
            sysmdl_m = cv_target.shape[1]
            sysmdl_n = cv_input.shape[1]
            sysmdl_T = train_input.shape[2]
            sysmdl_T_test = cv_input.shape[2]
            # Init MSE Loss
            self.MSE_cv_linear_epoch = torch.zeros([self.N_steps])
            self.MSE_cv_dB_epoch = torch.zeros([self.N_steps])
            self.MSE_train_linear_epoch = torch.zeros([self.N_steps])
            self.MSE_train_dB_epoch = torch.zeros([self.N_steps])
            
            if MaskOnState:
                mask = torch.tensor([True,False,False])
                if sysmdl_m == 2: 
                    mask = torch.tensor([True,False])

            ##############
            ### Epochs ###
            ##############

            self.MSE_cv_dB_opt = 1000
            self.MSE_cv_idx_opt = 0

            for ti in range(0, self.N_steps):

                ###############################
                ### Training Sequence Batch ###
                ###############################
                self.optimizer.zero_grad()
                # Training Mode
                self.hnet.train()
                self.mnet.train()
                self.mnet.batch_size = self.N_B
                # Init Hidden State
                self.hnet.init_hidden()
                self.mnet.init_hidden()

                # Init Training Batch tensors
                y_training_batch = torch.zeros([self.N_B, sysmdl_n, sysmdl_T]).to(self.device)
                train_target_batch = torch.zeros([self.N_B, sysmdl_m, sysmdl_T]).to(self.device)
                x_out_training_batch = torch.zeros([self.N_B, sysmdl_m, sysmdl_T]).to(self.device)
                if self.args.randomLength:
                    MSE_train_linear_LOSS = torch.zeros([self.N_B])
                    MSE_cv_linear_LOSS = torch.zeros([self.N_CV])

                # Randomly select N_B training sequences
                assert self.N_B <= self.N_E # N_B must be smaller than N_E
                n_e = random.sample(range(self.N_E), k=self.N_B)
                ii = 0
                for index in n_e:
                    if self.args.randomLength:
                        y_training_batch[ii,:,train_lengthMask[index,:]] = train_input[index,:,train_lengthMask[index,:]]
                        train_target_batch[ii,:,train_lengthMask[index,:]] = train_target[index,:,train_lengthMask[index,:]]
                    else:
                        y_training_batch[ii,:,:] = train_input[index]
                        train_target_batch[ii,:,:] = train_target[index]
                    ii += 1
                
                # Init Sequence
                train_init_batch = torch.empty([self.N_B, sysmdl_m,1]).to(self.device)
                ii = 0
                for index in n_e:
                    train_init_batch[ii,:,0] = torch.squeeze(train_init[index])
                    ii += 1
                self.mnet.InitSequence(train_init_batch, sysmdl_T)
                
                # Forward Computation
                weights = self.hnet(SoW_train)
                for t in range(0, sysmdl_T):
                    x_out_training_batch[:, :, t] = torch.squeeze(self.mnet(torch.unsqueeze(y_training_batch[:, :, t],2), weights=weights))
                
                # Compute Training Loss
                MSE_trainbatch_linear_LOSS = 0
                if(MaskOnState):
                    if self.args.randomLength:
                        jj = 0
                        for index in n_e:# mask out the padded part when computing loss
                            MSE_train_linear_LOSS[jj] = self.loss_fn(x_out_training_batch[jj,mask,train_lengthMask[index]], train_target_batch[jj,mask,train_lengthMask[index]])
                            jj += 1
                        MSE_trainbatch_linear_LOSS = torch.mean(MSE_train_linear_LOSS)
                    else:
                        MSE_trainbatch_linear_LOSS = self.loss_fn(x_out_training_batch[:,mask,:], train_target_batch[:,mask,:])
                else: # no mask on state
                    if self.args.randomLength:
                        jj = 0
                        for index in n_e:# mask out the padded part when computing loss
                            MSE_train_linear_LOSS[jj] = self.loss_fn(x_out_training_batch[jj,:,train_lengthMask[index]], train_target_batch[jj,:,train_lengthMask[index]])
                            jj += 1
                        MSE_trainbatch_linear_LOSS = torch.mean(MSE_train_linear_LOSS)
                    else: 
                        MSE_trainbatch_linear_LOSS = self.loss_fn(x_out_training_batch, train_target_batch)

                # dB Loss
                self.MSE_train_linear_epoch[ti] = MSE_trainbatch_linear_LOSS.item()
                self.MSE_train_dB_epoch[ti] = 10 * torch.log10(self.MSE_train_linear_epoch[ti])

                ##################
                ### Optimizing ###
                ##################

                # Before the backward pass, use the optimizer object to zero all of the
                # gradients for the variables it will update (which are the learnable
                # weights of the model). This is because by default, gradients are
                # accumulated in buffers( i.e, not overwritten) whenever .backward()
                # is called. Checkout docs of torch.autograd.backward for more details.

                # Backward pass: compute gradient of the loss with respect to model
                # parameters
                MSE_trainbatch_linear_LOSS.backward(retain_graph=True)

                # Calling the step function on an Optimizer makes an update to its
                # parameters
                self.optimizer.step()
                # self.scheduler.step(self.MSE_cv_dB_epoch[ti])

                #################################
                ### Validation Sequence Batch ###
                #################################

                # Cross Validation Mode
                self.hnet.eval()
                self.mnet.eval()
                self.mnet.batch_size = self.N_CV
                # Init Hidden State
                self.hnet.init_hidden()
                self.mnet.init_hidden()

                with torch.no_grad():
                    x_out_cv_batch = torch.empty([self.N_CV, sysmdl_m, sysmdl_T_test]).to(self.device)
                    
                    # Init Sequence                    
                    self.mnet.InitSequence(cv_init, sysmdl_T_test)                       

                    
                    weights = self.hnet(SoW_cv)
                    for t in range(0, sysmdl_T_test):
                        x_out_cv_batch[:, :, t] = torch.squeeze(self.mnet(torch.unsqueeze(cv_input[:, :, t],2), weights=weights))
                    
                    # Compute CV Loss
                    MSE_cvbatch_linear_LOSS = 0
                    if(MaskOnState):
                        if self.args.randomLength:
                            for index in range(self.N_CV):
                                MSE_cv_linear_LOSS[index] = self.loss_fn(x_out_cv_batch[index,mask,cv_lengthMask[index]], cv_target[index,mask,cv_lengthMask[index]])
                            MSE_cvbatch_linear_LOSS = torch.mean(MSE_cv_linear_LOSS)
                        else:          
                            MSE_cvbatch_linear_LOSS = self.loss_fn(x_out_cv_batch[:,mask,:], cv_target[:,mask,:])
                    else:
                        if self.args.randomLength:
                            for index in range(self.N_CV):
                                MSE_cv_linear_LOSS[index] = self.loss_fn(x_out_cv_batch[index,:,cv_lengthMask[index]], cv_target[index,:,cv_lengthMask[index]])
                            MSE_cvbatch_linear_LOSS = torch.mean(MSE_cv_linear_LOSS)
                        else:
                            MSE_cvbatch_linear_LOSS = self.loss_fn(x_out_cv_batch, cv_target)

                    # dB Loss
                    self.MSE_cv_linear_epoch[ti] = MSE_cvbatch_linear_LOSS.item()
                    self.MSE_cv_dB_epoch[ti] = 10 * torch.log10(self.MSE_cv_linear_epoch[ti])
                    
                    if (self.MSE_cv_dB_epoch[ti] < self.MSE_cv_dB_opt):
                        self.MSE_cv_dB_opt = self.MSE_cv_dB_epoch[ti]
                        self.MSE_cv_idx_opt = ti
                        
                        torch.save(self.hnet, path_results + 'hnet_best-model.pt')
                        torch.save(self.mnet, path_results + 'mnet_best-model.pt')

                ########################
                ### Training Summary ###
                ########################
                print(ti, "MSE Training :", self.MSE_train_dB_epoch[ti], "[dB]", "MSE Validation :", self.MSE_cv_dB_epoch[ti],
                    "[dB]")
                        
                if (ti > 1):
                    d_train = self.MSE_train_dB_epoch[ti] - self.MSE_train_dB_epoch[ti - 1]
                    d_cv = self.MSE_cv_dB_epoch[ti] - self.MSE_cv_dB_epoch[ti - 1]
                    print("diff MSE Training :", d_train, "[dB]", "diff MSE Validation :", d_cv, "[dB]")

                print("Optimal idx:", self.MSE_cv_idx_opt, "Optimal :", self.MSE_cv_dB_opt, "[dB]")

            return [self.MSE_cv_linear_epoch, self.MSE_cv_dB_epoch, self.MSE_train_linear_epoch, self.MSE_train_dB_epoch]

    def NNTest(self, sys_model, test_input_tuple, test_target_tuple, path_results,test_init,\
        MaskOnState=False,load_model=False,load_model_path=None, test_lengthMask=None):
        if self.args.wandb_switch: 
            import wandb
        # Load model
        if load_model:
            self.hnet = torch.load(load_model_path[0], map_location=self.device)
            self.mnet = torch.load(load_model_path[1], map_location=self.device) 
        else:
            self.hnet = torch.load(path_results+'hnet_best-model.pt', map_location=self.device)
            self.mnet = torch.load(path_results+'mnet_best-model.pt', map_location=self.device) 
        
        # SoW
        assert torch.allclose(test_input_tuple[1], test_target_tuple[1]) 
        SoW_test = test_input_tuple[1]
        self.mnet.UpdateSystemDynamics(sys_model)
        # load data
        test_input = test_input_tuple[0]
        test_target = test_target_tuple[0]
        # data size
        self.N_T = test_input.shape[0]
        sysmdl_m = test_target.size()[1]
        sysmdl_T_test = test_target.size()[2]

        self.MSE_test_linear_arr = torch.zeros([self.N_T])
        x_out_test = torch.zeros([self.N_T, sysmdl_m,sysmdl_T_test]).to(self.device)

        if MaskOnState:
            mask = torch.tensor([True,False,False])
            if sysmdl_m == 2: 
                mask = torch.tensor([True,False])

        # MSE LOSS Function
        loss_fn = nn.MSELoss(reduction='mean')

        # Test mode
        self.hnet.eval()
        self.mnet.eval()
        self.mnet.batch_size = self.N_T
        # Init Hidden State
        self.hnet.init_hidden()
        self.mnet.init_hidden()

        torch.no_grad()

        start = time.time()

        # Init Sequence
        self.mnet.InitSequence(test_init, sysmdl_T_test)               
        
        weights = self.hnet(SoW_test)
        for t in range(0, sysmdl_T_test):
            x_out_test[:,:, t] = torch.squeeze(self.mnet(torch.unsqueeze(test_input[:,:, t],2), weights=weights))
        
        end = time.time()
        t = end - start

        # MSE loss
        for j in range(self.N_T):# cannot use batch due to different length and std computation  
            if(MaskOnState):
                if self.args.randomLength:
                    self.MSE_test_linear_arr[j] = loss_fn(x_out_test[j,mask,test_lengthMask[j]], test_target[j,mask,test_lengthMask[j]]).item()
                else:
                    self.MSE_test_linear_arr[j] = loss_fn(x_out_test[j,mask,:], test_target[j,mask,:]).item()
            else:
                if self.args.randomLength:
                    self.MSE_test_linear_arr[j] = loss_fn(x_out_test[j,:,test_lengthMask[j]], test_target[j,:,test_lengthMask[j]]).item()
                else:
                    self.MSE_test_linear_arr[j] = loss_fn(x_out_test[j,:,:], test_target[j,:,:]).item()
        
        # Average
        self.MSE_test_linear_avg = torch.mean(self.MSE_test_linear_arr)
        self.MSE_test_dB_avg = 10 * torch.log10(self.MSE_test_linear_avg)

        # Standard deviation
        self.MSE_test_linear_std = torch.std(self.MSE_test_linear_arr, unbiased=True)

        # Confidence interval
        self.test_std_dB = 10 * torch.log10(self.MSE_test_linear_std + self.MSE_test_linear_avg) - self.MSE_test_dB_avg

        # Print MSE and std
        str = self.modelName + "-" + "MSE Test:"
        print(str, self.MSE_test_dB_avg, "[dB]")
        str = self.modelName + "-" + "STD Test:"
        print(str, self.test_std_dB, "[dB]")
        # Print Run Time
        print("Inference Time:", t)

        ### Optinal: record loss on wandb
        if self.args.wandb_switch:
            wandb.log({'test_loss':self.MSE_test_dB_avg})
        ###

        return [self.MSE_test_linear_arr, self.MSE_test_linear_avg, self.MSE_test_dB_avg, x_out_test, t]
