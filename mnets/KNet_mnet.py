"""# **Class: KalmanNet as main network**"""

import torch
import torch.nn as nn
import torch.nn.functional as F

class KalmanNetNN(torch.nn.Module):

    ###################
    ### Constructor ###
    ###################
    def __init__(self):
        super().__init__()
    
    def NNBuild(self, SysModel, args):

        # Device
        if args.use_cuda:
            self.device = torch.device('cuda')
        else:
            self.device = torch.device('cpu')

        self.activation_func = nn.ReLU()
        self.use_context_mod = args.use_context_mod

        self.InitSystemDynamics(SysModel.f, SysModel.h, SysModel.m, SysModel.n)

        self.InitKGainNet(SysModel.prior_Q, SysModel.prior_Sigma, SysModel.prior_S, args)
       
        return self.n_params_KNet

    ######################################
    ### Initialize Kalman Gain Network ###
    ######################################
    def InitKGainNet(self, prior_Q, prior_Sigma, prior_S, args):

        self.seq_len_input = 1 # KNet calculates time-step by time-step
        self.batch_size = args.n_batch # Batch size

        self.prior_Q = prior_Q.to(self.device)
        self.prior_Sigma = prior_Sigma.to(self.device)
        self.prior_S = prior_S.to(self.device)

        self.out_Q = self.prior_Q.flatten().reshape(1,1, -1).repeat(self.seq_len_input,self.batch_size, 1)
        self.out_Sigma = self.prior_Sigma.flatten().reshape(1,1, -1).repeat(self.seq_len_input,self.batch_size, 1)
        self.out_S = self.prior_S.flatten().reshape(1,1, -1).repeat(self.seq_len_input,self.batch_size, 1)

        ### Define network dimensions ###
        # lstm to track Q
        d_input_Q = self.m * args.in_mult_KNet
        d_hidden_Q = self.m ** 2
        # lstm to track Sigma
        d_input_Sigma = d_hidden_Q + self.m * args.in_mult_KNet
        d_hidden_Sigma = self.m ** 2  
        # lstm to track S
        d_input_S = self.n ** 2 + 2 * self.n * args.in_mult_KNet
        d_hidden_S = self.n ** 2       
        # Fully connected 1
        d_input_FC1 = d_hidden_Sigma
        d_output_FC1 = self.n ** 2
        # Fully connected 2
        d_input_FC2 = d_hidden_S + d_hidden_Sigma
        d_output_FC2 = self.n * self.m
        d_hidden_FC2 = d_input_FC2 * args.out_mult_KNet
        # Fully connected 3
        d_input_FC3 = d_hidden_S + d_output_FC2
        d_output_FC3 = self.m ** 2
        # Fully connected 4
        d_input_FC4 = d_hidden_Sigma + d_output_FC3
        d_output_FC4 = d_hidden_Sigma       
        # Fully connected 5
        d_input_FC5 = self.m
        d_output_FC5 = self.m * args.in_mult_KNet
        # Fully connected 6
        d_input_FC6 = self.m
        d_output_FC6 = self.m * args.in_mult_KNet       
        # Fully connected 7
        d_input_FC7 = 2 * self.n
        d_output_FC7 = 2 * self.n * args.in_mult_KNet

        # Define original KNet fc and lstm layer shapes for later internal layer construction
        self.fc_shape = {
            'fc1_w': [d_output_FC1, d_input_FC1],
            'fc1_b': [d_output_FC1],
            'fc2_w1': [d_hidden_FC2, d_input_FC2],
            'fc2_b1': [d_hidden_FC2],
            'fc2_w2': [d_output_FC2, d_hidden_FC2],
            'fc2_b2': [d_output_FC2],
            'fc3_w': [d_output_FC3, d_input_FC3],
            'fc3_b': [d_output_FC3],
            'fc4_w': [d_output_FC4, d_input_FC4],
            'fc4_b': [d_output_FC4],
            'fc5_w': [d_output_FC5, d_input_FC5],
            'fc5_b': [d_output_FC5],
            'fc6_w': [d_output_FC6, d_input_FC6],
            'fc6_b': [d_output_FC6],
            'fc7_w': [d_output_FC7, d_input_FC7],
            'fc7_b': [d_output_FC7]}
        
        self.lstm_shape = {
            'lstm_q_w_ih': [d_hidden_Q * 4, d_input_Q],
            'lstm_q_b_ih': [d_hidden_Q * 4],
            'lstm_q_w_hh': [d_hidden_Q * 4, d_hidden_Q],
            'lstm_q_b_hh': [d_hidden_Q * 4],
            'lstm_sigma_w_ih': [d_hidden_Sigma * 4, d_input_Sigma],
            'lstm_sigma_b_ih': [d_hidden_Sigma * 4],
            'lstm_sigma_w_hh': [d_hidden_Sigma * 4, d_hidden_Sigma],
            'lstm_sigma_b_hh': [d_hidden_Sigma * 4],
            'lstm_s_w_ih': [d_hidden_S * 4, d_input_S],
            'lstm_s_b_ih': [d_hidden_S * 4],
            'lstm_s_w_hh': [d_hidden_S * 4, d_hidden_S],
            'lstm_s_b_hh': [d_hidden_S * 4]}
        
        # Calculate number of parameters in KNet
        n_params_fc = d_output_FC1*(d_input_FC1 +1)+d_hidden_FC2*(d_input_FC2 +1)+d_output_FC2*(d_hidden_FC2 +1)+d_output_FC3*(d_input_FC3 +1)+d_output_FC4*(d_input_FC4 +1)+d_output_FC5*(d_input_FC5 +1)+d_output_FC6*(d_input_FC6 +1)+d_output_FC7*(d_input_FC7 +1)
        n_params_lstm = d_hidden_Q*(d_input_Q +1)*4+d_hidden_Sigma*(d_input_Sigma +1)*4+d_hidden_S*(d_input_S +1)*4 +\
                        d_hidden_Q * 4 * (d_hidden_Q +1) + d_hidden_Sigma * 4 * (d_hidden_Sigma +1) + d_hidden_S * 4 * (d_hidden_S +1)
        self.n_params_KNet = n_params_fc + n_params_lstm
        
    #######################
    ### System Dynamics ###
    #######################
    def InitSystemDynamics(self, f, h, m, n):
        
        # Set State Evolution Function
        self.f = f
        self.m = m

        # Set Observation Function
        self.h = h
        self.n = n

    def UpdateSystemDynamics(self, SysModel):
        
        # Set State Evolution Function
        self.f = SysModel.f
        self.m = SysModel.m

        # Set Observation Function
        self.h = SysModel.h
        self.n = SysModel.n

    ###########################
    ### Initialize Sequence ###
    ###########################
    def InitSequence(self, M1_0, T):
        """
        input M1_0 (torch.tensor): 1st moment of x at time 0 [batch_size, m, 1]
        """
        self.T = T

        self.m1x_posterior = M1_0.to(self.device)
        self.m1x_posterior_previous = self.m1x_posterior
        self.m1x_prior_previous = self.m1x_posterior
        self.y_previous = self.h(self.m1x_posterior)

    ######################
    ### Compute Priors ###
    ######################
    def step_prior(self):
        # Predict the 1-st moment of x
        self.m1x_prior = self.f(self.m1x_posterior)

        # Predict the 1-st moment of y
        self.m1y = self.h(self.m1x_prior)

    ##############################
    ### Kalman Gain Estimation ###
    ##############################
    def step_KGain_est(self, y, FC_weights, lstm_weights):
        # both in size [batch_size, n]
        obs_diff = torch.squeeze(y,2) - torch.squeeze(self.y_previous,2) 
        obs_innov_diff = torch.squeeze(y,2) - torch.squeeze(self.m1y,2)
        # both in size [batch_size, m]
        fw_evol_diff = torch.squeeze(self.m1x_posterior,2) - torch.squeeze(self.m1x_posterior_previous,2)
        fw_update_diff = torch.squeeze(self.m1x_posterior,2) - torch.squeeze(self.m1x_prior_previous,2)

        obs_diff = F.normalize(obs_diff, p=2, dim=1, eps=1e-12, out=None)
        obs_innov_diff = F.normalize(obs_innov_diff, p=2, dim=1, eps=1e-12, out=None)
        fw_evol_diff = F.normalize(fw_evol_diff, p=2, dim=1, eps=1e-12, out=None)
        fw_update_diff = F.normalize(fw_update_diff, p=2, dim=1, eps=1e-12, out=None)

        # Kalman Gain Network Step
        KG = self.KGain_step(obs_diff, obs_innov_diff, fw_evol_diff, fw_update_diff, FC_weights, lstm_weights)

        # Reshape Kalman Gain to a Matrix
        self.KGain = torch.reshape(KG, (self.batch_size, self.m, self.n))

    #######################
    ### Kalman Net Step ###
    #######################
    def KNet_step(self, y, FC_weights, lstm_weights):

        # Compute Priors
        self.step_prior()

        # Compute Kalman Gain
        self.step_KGain_est(y, FC_weights, lstm_weights)

        # Innovation
        dy = y - self.m1y # [batch_size, n, 1]

        # Compute the 1-st posterior moment
        INOV = torch.bmm(self.KGain, dy)
        self.m1x_posterior_previous = self.m1x_posterior
        self.m1x_posterior = self.m1x_prior + INOV

        #self.state_process_posterior_0 = self.state_process_prior_0
        self.m1x_prior_previous = self.m1x_prior

        # update y_prev
        self.y_previous = y

        # return
        return self.m1x_posterior

    ########################
    ### Kalman Gain Step ###
    ########################
    def KGain_step(self, obs_diff, obs_innov_diff, fw_evol_diff, fw_update_diff, FC_weights, lstm_weights):

        def expand_dim(x):
            expanded = torch.empty(self.seq_len_input, self.batch_size, x.shape[-1]).to(self.device)
            expanded[0, :, :] = x
            return expanded

        obs_diff = expand_dim(obs_diff)
        obs_innov_diff = expand_dim(obs_innov_diff)
        fw_evol_diff = expand_dim(fw_evol_diff)
        fw_update_diff = expand_dim(fw_update_diff)
        
        ####################
        ### Forward Flow ###
        ####################
        
        # FC 5
        in_FC5 = fw_evol_diff
        out_FC5 = self.activation_func(F.linear(in_FC5, FC_weights['fc5_w'], bias=FC_weights['fc5_b']))

        # Q-lstm
        in_Q = out_FC5
        self.out_Q, self.h_Q = self.lstm_rnn_step(in_Q, (self.out_Q, self.h_Q), 
           [lstm_weights['lstm_q_w_ih'],
            lstm_weights['lstm_q_b_ih'],
            lstm_weights['lstm_q_w_hh'],
            lstm_weights['lstm_q_b_hh']])

        # FC 6
        in_FC6 = fw_update_diff
        out_FC6 = self.activation_func(F.linear(in_FC6, FC_weights['fc6_w'], bias=FC_weights['fc6_b']))

        # Sigma_lstm
        in_Sigma = torch.cat((self.out_Q, out_FC6), 2)
        self.out_Sigma, self.h_Sigma = self.lstm_rnn_step(in_Sigma, (self.out_Sigma, self.h_Sigma), 
           [lstm_weights['lstm_sigma_w_ih'],
            lstm_weights['lstm_sigma_b_ih'],
            lstm_weights['lstm_sigma_w_hh'],
            lstm_weights['lstm_sigma_b_hh']])

        # FC 1
        in_FC1 = self.out_Sigma
        out_FC1 = self.activation_func(F.linear(in_FC1, FC_weights['fc1_w'], bias=FC_weights['fc1_b']))

        # FC 7
        in_FC7 = torch.cat((obs_diff, obs_innov_diff), 2)
        out_FC7 = self.activation_func(F.linear(in_FC7, FC_weights['fc7_w'], bias=FC_weights['fc7_b']))


        # S-lstm
        in_S = torch.cat((out_FC1, out_FC7), 2)
        self.out_S, self.h_S = self.lstm_rnn_step(in_S, (self.out_S, self.h_S), 
           [lstm_weights['lstm_s_w_ih'],
            lstm_weights['lstm_s_b_ih'],
            lstm_weights['lstm_s_w_hh'],
            lstm_weights['lstm_s_b_hh']])

        # FC 2
        in_FC2 = torch.cat((self.out_Sigma, self.out_S), 2)
        out_FC2 = self.activation_func(F.linear(in_FC2, FC_weights['fc2_w1'], bias=FC_weights['fc2_b1']))
        out_FC2 = F.linear(out_FC2, FC_weights['fc2_w2'], bias=FC_weights['fc2_b2'])

        #####################
        ### Backward Flow ###
        #####################

        # FC 3
        in_FC3 = torch.cat((self.out_S, out_FC2), 2)
        out_FC3 = self.activation_func(F.linear(in_FC3, FC_weights['fc3_w'], bias=FC_weights['fc3_b']))

        # FC 4
        in_FC4 = torch.cat((self.out_Sigma, out_FC3), 2)
        out_FC4 = self.activation_func(F.linear(in_FC4, FC_weights['fc4_w'], bias=FC_weights['fc4_b']))

        # updating hidden state of the Sigma-lstm
        self.h_Sigma = out_FC4

        return out_FC2
    ###############
    ### Forward ###
    ###############
    def forward(self, y, weights = None):
        y = y.to(self.device)
        if weights is not None: # if weights are provided, use them
            weights = weights.to(self.device)
            FC_weights, lstm_weights = self.split_weights(weights)
        return self.KNet_step(y, FC_weights, lstm_weights)

    #########################
    ### Init Hidden State ###
    #########################
    def init_hidden(self):
        self.out_Q = self.prior_Q.flatten().reshape(1,1, -1).repeat(self.seq_len_input,self.batch_size, 1)
        self.out_Sigma = self.prior_Sigma.flatten().reshape(1,1, -1).repeat(self.seq_len_input,self.batch_size, 1)
        self.out_S = self.prior_S.flatten().reshape(1,1, -1).repeat(self.seq_len_input,self.batch_size, 1)

        self.h_S = torch.zeros(self.seq_len_input,self.batch_size,self.n ** 2) # batch size expansion   
        self.h_Sigma = torch.zeros(self.seq_len_input,self.batch_size,self.m ** 2) # batch size expansion
        self.h_Q = torch.zeros(self.seq_len_input,self.batch_size,self.m ** 2) # batch size expansion

    #####################
    ### Split weights ###
    #####################
    def split_weights(self, weights):
        """
        input: weights torch.tensor [total number of weights]
        """
        weight_index = 0
        # split weights and biases for FC 1 - 7
        def split_and_reshape_fc(weights, weight_index, shape_w, shape_b):
            length_w = shape_w[0] * shape_w[1]
            length_b = shape_b[0]
            fc_w = weights[weight_index:weight_index+length_w].reshape(shape_w[0], shape_w[1])
            weight_index = weight_index + length_w
            fc_b = weights[weight_index:weight_index+length_b].reshape(shape_b[0])
            weight_index = weight_index + length_b
            return fc_w, fc_b, weight_index
        
        fc1_w, fc1_b, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc1_w'], self.fc_shape['fc1_b'])
        fc2_w1, fc2_b1, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc2_w1'], self.fc_shape['fc2_b1'])
        fc2_w2, fc2_b2, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc2_w2'], self.fc_shape['fc2_b2'])
        fc3_w, fc3_b, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc3_w'], self.fc_shape['fc3_b'])
        fc4_w, fc4_b, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc4_w'], self.fc_shape['fc4_b'])
        fc5_w, fc5_b, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc5_w'], self.fc_shape['fc5_b'])
        fc6_w, fc6_b, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc6_w'], self.fc_shape['fc6_b'])
        fc7_w, fc7_b, weight_index = split_and_reshape_fc(weights, weight_index, self.fc_shape['fc7_w'], self.fc_shape['fc7_b'])

        FC_weights = {
            'fc1_w': fc1_w,
            'fc1_b': fc1_b,
            'fc2_w1': fc2_w1,
            'fc2_b1': fc2_b1,
            'fc2_w2': fc2_w2,
            'fc2_b2': fc2_b2,
            'fc3_w': fc3_w,
            'fc3_b': fc3_b,
            'fc4_w': fc4_w,
            'fc4_b': fc4_b,
            'fc5_w': fc5_w,
            'fc5_b': fc5_b,
            'fc6_w': fc6_w,
            'fc6_b': fc6_b,
            'fc7_w': fc7_w,
            'fc7_b': fc7_b}

        # split weights and biases for lstm Q, Sigma, S
        def split_and_reshape_lstm(weights, weight_index, shape_w_ih, shape_b_ih, shape_w_hh, shape_b_hh):
            length_w_ih = shape_w_ih[0] * shape_w_ih[1]
            length_b_ih = shape_b_ih[0]
            length_w_hh = shape_w_hh[0] * shape_w_hh[1]
            length_b_hh = shape_b_hh[0]
            lstm_w_ih = weights[weight_index:weight_index+length_w_ih].reshape(shape_w_ih[0], shape_w_ih[1])
            weight_index = weight_index + length_w_ih
            lstm_b_ih = weights[weight_index:weight_index+length_b_ih].reshape(shape_b_ih[0])
            weight_index = weight_index + length_b_ih
            lstm_w_hh = weights[weight_index:weight_index+length_w_hh].reshape(shape_w_hh[0], shape_w_hh[1])
            weight_index = weight_index + length_w_hh
            lstm_b_hh = weights[weight_index:weight_index+length_b_hh].reshape(shape_b_hh[0])
            weight_index = weight_index + length_b_hh
            return lstm_w_ih, lstm_b_ih, lstm_w_hh, lstm_b_hh, weight_index
        
        lstm_q_w_ih, lstm_q_b_ih, lstm_q_w_hh, lstm_q_b_hh, weight_index = split_and_reshape_lstm(weights, weight_index, self.lstm_shape['lstm_q_w_ih'], self.lstm_shape['lstm_q_b_ih'], self.lstm_shape['lstm_q_w_hh'], self.lstm_shape['lstm_q_b_hh'])
        lstm_sigma_w_ih, lstm_sigma_b_ih, lstm_sigma_w_hh, lstm_sigma_b_hh, weight_index = split_and_reshape_lstm(weights, weight_index, self.lstm_shape['lstm_sigma_w_ih'], self.lstm_shape['lstm_sigma_b_ih'], self.lstm_shape['lstm_sigma_w_hh'], self.lstm_shape['lstm_sigma_b_hh'])
        lstm_s_w_ih, lstm_s_b_ih, lstm_s_w_hh, lstm_s_b_hh, weight_index = split_and_reshape_lstm(weights, weight_index, self.lstm_shape['lstm_s_w_ih'], self.lstm_shape['lstm_s_b_ih'], self.lstm_shape['lstm_s_w_hh'], self.lstm_shape['lstm_s_b_hh'])

        lstm_weights = {
            'lstm_q_w_ih': lstm_q_w_ih,
            'lstm_q_b_ih': lstm_q_b_ih,
            'lstm_q_w_hh': lstm_q_w_hh,
            'lstm_q_b_hh': lstm_q_b_hh,
            'lstm_sigma_w_ih': lstm_sigma_w_ih,
            'lstm_sigma_b_ih': lstm_sigma_b_ih,
            'lstm_sigma_w_hh': lstm_sigma_w_hh,
            'lstm_sigma_b_hh': lstm_sigma_b_hh,
            'lstm_s_w_ih': lstm_s_w_ih,
            'lstm_s_b_ih': lstm_s_b_ih,
            'lstm_s_w_hh': lstm_s_w_hh,
            'lstm_s_b_hh': lstm_s_b_hh}

        return FC_weights, lstm_weights

    ########################
    ### LSTM computation ###
    ########################    
    def lstm_rnn_step(self, x_t, h_t, lstm_weights):
        """
        Args:
            x_t: Tensor of size ``[1, batch_size, n_inputs]`` with inputs.
            h_t (tuple): (y_t, c_t) Tuple of length 2, containing two tensors of size
                ``[batch_size, n_hidden]`` with previous output y and c.
            lstm_weights: List of length 4, containing weights and biases for
                the LSTM layer.
           
        Returns:
            - **y_t** (torch.Tensor): The tensor ``y_t`` of size
              ``[1, batch_size, n_hidden]`` with the output state.
            - **c_t** (torch.Tensor): The tensor ``c_t`` of size
              ``[1, batch_size, n_hidden]`` with the new cell state.
        """

        c_t = h_t[1]
        y_t = h_t[0]

        assert len(lstm_weights) == 4
        weight_ih = lstm_weights[0]
        bias_ih = lstm_weights[1]
        weight_hh = lstm_weights[2]
        bias_hh = lstm_weights[3]

        d_hidden = weight_hh.shape[1]

        # Compute total pre-activation input.
        gates = x_t @ weight_ih.t() + y_t @ weight_hh.t()
        gates = gates + bias_ih + bias_hh

        i_t = gates[:, :, :d_hidden]
        f_t = gates[:, :, d_hidden:d_hidden*2]
        g_t = gates[:, :, d_hidden*2:d_hidden*3]
        o_t = gates[:, :, d_hidden*3:]

        # Compute activation.
        i_t = torch.sigmoid(i_t) # input
        f_t = torch.sigmoid(f_t) # forget
        g_t = torch.tanh(g_t)
        o_t = torch.sigmoid(o_t) # output

        # Compute c states.
        c_t = f_t * c_t + i_t * g_t

        # Compute h states.
        y_t = o_t * torch.tanh(c_t)
        
        return y_t, c_t
    