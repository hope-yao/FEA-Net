import numpy as np
import scipy.io as sio
import tensorflow as tf
import matplotlib.pyplot as plt
from tqdm import tqdm


class FEA_Net_h():
    # NOTICE: right now for homogeneous anisotropic material only!!
    def __init__(self, data, cfg):
        # set learning rate
        self.lr = cfg['lr']
        self.num_epoch = cfg['epoch']
        # self.batch_size = 4

        # data related
        self.num_node = data['num_node']
        self.E, self.mu, self.k, self.alpha = self.rho = data['rho'] #

        # 3 dimensional in and out, defined on the nodes
        self.load_pl = tf.placeholder(tf.float32, shape=(None, data['num_node'], data['num_node'], 3))
        self.resp_pl = tf.placeholder(tf.float32, shape=(None, data['num_node'], data['num_node'], 3))

        # get filters
        self.get_w_matrix()


    def get_w_matrix(self):
        self.get_w_matrix_elast()
        self.get_w_matrix_thermal()
        self.get_w_matrix_coupling()
        self.apply_physics_constrain()

    def apply_physics_constrain(self):
        # known physics
        wxx = tf.constant(self.wxx)
        wyy = tf.constant(self.wyy)
        wxy = tf.constant(self.wxy)
        wyx = tf.constant(self.wyx)
        wtt = tf.constant(self.wtt)
        # unknown physics
        self.wtx = wtx = tf.Variable(self.wtx)
        self.wty = wty = tf.Variable(self.wty)
        self.wxt = wxt = tf.Variable(self.wxt)
        self.wyt = wyt =tf.Variable(self.wyt)

        # add constrains
        self.singula_penalty = tf.abs(tf.reduce_sum(wtx)) \
                               + tf.abs(tf.reduce_sum(wty)) \
                               + tf.abs(tf.reduce_sum(wxt))\
                               + tf.abs(tf.reduce_sum(wyt))
        # self.E = tf.clip_by_value(self.E, 0, 1)
        # self.mu = tf.clip_by_value(self.mu, 0, 0.5)

        # tf.nn.conv2d filter shape: [filter_height, filter_width, in_channels, out_channels]
        self.w_filter = tf.concat([tf.concat([wxx, wxy, wxt],2),
                                   tf.concat([wyx, wyy, wyt],2),
                                   tf.concat([wtx, wty, wtt],2)],
                                  3)
        self.trainable_var = [wtx, wty, wxt, wyt]

    def get_w_matrix_coupling(self):
        E, v = self.E, self.mu
        alpha = self.alpha
        self.wtx_ref = np.zeros((3,3,1,1), dtype='float32')
        self.wty_ref = np.zeros((3,3,1,1), dtype='float32')
        coef = E * alpha / (6*(v-1)) / 400
        self.wxt_ref = coef * np.asarray([[1, 0, -1],
                                      [4, 0, -4],
                                      [1, 0, -1]]
                                     , dtype='float32').reshape(3,3,1,1)

        self.wyt_ref = coef * np.asarray([[-1, -4, -1],
                                      [0, 0, 0],
                                      [1, 4, 1]]
                                     , dtype='float32').reshape(3,3,1,1)
        self.wtx = self.wtx_ref * 1.1
        self.wty = self.wty_ref * 1.1
        self.wxt = self.wxt_ref * 1.1
        self.wyt = self.wyt_ref * 1.1

    def get_w_matrix_thermal(self):
        w = -1/3. * self.k * np.asarray([[1., 1., 1.], [1., -8., 1.], [1., 1., 1.]])
        w = np.asarray(w, dtype='float32')
        self.wtt = w.reshape(3,3,1,1)

    def get_w_matrix_elast(self):
        E, mu = self.E, self.mu
        cost_coef = E / 16. / (1 - mu ** 2)
        wxx = cost_coef * np.asarray([
            [-4 * (1 - mu / 3.), 16 * mu / 3., -4 * (1 - mu / 3.)],
            [-8 * (1 + mu / 3.), 32. * (1 - mu / 3.), -8 * (1 + mu / 3.)],
            [-4 * (1 - mu / 3.), 16 * mu / 3., -4 * (1 - mu / 3.)],
        ], dtype='float32')

        wxy = wyx = cost_coef * np.asarray([
            [2 * (mu + 1), 0, -2 * (mu + 1)],
            [0, 0, 0],
            [-2 * (mu + 1), 0, 2 * (mu + 1)],
        ], dtype='float32')

        wyy = cost_coef * np.asarray([
            [-4 * (1 - mu / 3.), -8 * (1 + mu / 3.), -4 * (1 - mu / 3.)],
            [16 * mu / 3., 32. * (1 - mu / 3.), 16 * mu / 3.],
            [-4 * (1 - mu / 3.), -8 * (1 + mu / 3.), -4 * (1 - mu / 3.)],
        ], dtype='float32')

        self.wxx = wxx.reshape(3,3,1,1)
        self.wxy = wxy.reshape(3,3,1,1)
        self.wyx = wyx.reshape(3,3,1,1)
        self.wyy = wyy.reshape(3,3,1,1)

    def boundary_padding(self,x):
        ''' special symmetric boundary padding '''
        left = x[:, :, 1:2, :]
        right = x[:, :, -2:-1, :]
        upper = tf.concat([x[:, 1:2, 1:2, :], x[:, 1:2, :, :], x[:, 1:2, -2:-1, :]], 2)
        down = tf.concat([x[:, -2:-1, 1:2, :], x[:, -2:-1, :, :], x[:, -2:-1, -2:-1, :]], 2)
        padded_x = tf.concat([left, x, right], 2)
        padded_x = tf.concat([upper, padded_x, down], 1)
        return padded_x

    def forward_pass(self):
        padded_resp = self.boundary_padding(self.resp_pl)  # for boundary consideration
        wx = tf.nn.conv2d(input=padded_resp, filter=self.w_filter, strides=[1, 1, 1, 1], padding='VALID')
        self.load_pred = wx

    def get_loss(self):
        self.forward_pass()
        self.l1_error = tf.reduce_mean(tf.abs(self.load_pred - self.load_pl))
        self.loss = self.l1_error + self.singula_penalty

    def get_optimizer(self):
        self.get_loss()
        self.optimizer = tf.train.AdamOptimizer(self.lr, beta1=0.5)
        # self.optimizer = tf.train.MomentumOptimizer(learning_rate=self.lr, momentum=0.99)
        self.grads = self.optimizer.compute_gradients(self.loss, var_list=self.trainable_var)
        self.train_op = self.optimizer.apply_gradients(self.grads)

    def initial_graph(self):
        # initialize
        FLAGS = tf.app.flags.FLAGS
        tfconfig = tf.ConfigProto(
            allow_soft_placement=True,
            log_device_placement=True,
        )
        tfconfig.gpu_options.allow_growth = True
        self.sess = tf.Session(config=tfconfig)
        init = tf.global_variables_initializer()
        self.sess.run(init)

    def run_training(self, data):
        res_hist = {
            'test_pred': np.zeros((self.num_epoch, data['test_load'].shape[0], self.num_node, self.num_node, 3)),
            'loss': np.zeros((self.num_epoch, 2)), # train, test
            'l1_error': np.zeros((self.num_epoch, 2)), # train, test
            'singula_penalty': np.zeros((self.num_epoch, 2)), # train, test
            'wtx': np.zeros((self.num_epoch, 3, 3)),
            'wty': np.zeros((self.num_epoch, 3, 3)),
            'wxt': np.zeros((self.num_epoch, 3, 3)),
            'wyt': np.zeros((self.num_epoch, 3, 3)),
            # 'E_hist': np.zeros((self.num_epoch, 1)),
            # 'mu_hist': np.zeros((self.num_epoch, 1)),
            # 'k': np.zeros((self.num_epoch, 1)),
        }

        self.op_list = [
                          self.load_pred,
                          self.loss,
                          self.l1_error,
                          self.singula_penalty,
                          tf.squeeze(self.wtx),
                          tf.squeeze(self.wty),
                          tf.squeeze(self.wxt),
                          tf.squeeze(self.wyt),
                          # self.E,
                          # self.mu,
                          # self.k,
                        ]

        # max_iter = data['load'].shape[0] // self.batch_size
        # for itr in range(max_iter):
        #     feed_dict_train = {self.load_pl: data['load'][itr:itr + 1], self.resp_pl: data['resp'][itr:itr + 1]}

        for ep_i in tqdm(range(self.num_epoch)):

            # Training
            feed_dict_train = {self.load_pl: data['train_load'], self.resp_pl: data['train_resp']}
            res_hist['loss'][ep_i, 0],\
            res_hist['l1_error'][ep_i, 0],\
            res_hist['singula_penalty'][ep_i, 0] = self.sess.run([self.loss,self.l1_error,self.singula_penalty], feed_dict_train)

            # Testing
            feed_dict_test = {self.load_pl: data['test_load'], self.resp_pl: data['test_resp']}
            res_hist['test_pred'][ep_i],\
            res_hist['loss'][ep_i, 1],\
            res_hist['l1_error'][ep_i, 1],\
            res_hist['singula_penalty'][ep_i, 1],\
            res_hist['wtx'][ep_i],\
            res_hist['wty'][ep_i],\
            res_hist['wxt'][ep_i],\
            res_hist['wyt'][ep_i]  = self.sess.run(self.op_list, feed_dict_test)

            self.sess.run(self.train_op, feed_dict_train)
            print('epoch: {},  training loss: {},  testing loss: {}'.format(ep_i,res_hist['loss'][ep_i,0],res_hist['loss'][ep_i,1]))
            np.save('therm_elast_res_hist', res_hist)

        return res_hist

def visualize(data, res_hist):
    plt.figure()
    plt.subplot(1,3,1)
    plt.plot(res_hist['loss'][:,0], label='train')
    plt.plot(res_hist['loss'][:,1], label='test')
    plt.title('loss')
    plt.legend()
    plt.subplot(1,3,2)
    plt.plot(res_hist['l1_error'][:,0], label='train')
    plt.plot(res_hist['l1_error'][:,1], label='test')
    plt.title('l1_error')
    plt.legend()
    plt.subplot(1,3,3)
    plt.plot(res_hist['singula_penalty'][:,0], label='train')
    plt.plot(res_hist['singula_penalty'][:,1], label='test')
    plt.title('sigular penalty')
    plt.legend()
    # plt.savefig()

    plt.figure(figsize=(6,6))
    idx = 0 # which data to visualize
    for i in range(3):
        plt.subplot(4, 3, i+1)
        plt.imshow(data['test_resp'][idx,1:-1,1:-1,i])
        plt.colorbar()
        plt.subplot(4, 3, 3+i+1)
        plt.imshow(data['test_load'][idx,1:-1,1:-1,i])
        plt.colorbar()
        plt.subplot(4, 3, 6+i+1)
        plt.imshow(res_hist['test_pred'][-1,idx,1:-1,1:-1,i])
        plt.colorbar()
        plt.subplot(4, 3, 9 + i + 1)
        plt.imshow(data['test_load'][idx,1:-1,1:-1,i]-res_hist['test_pred'][-1, idx, 1:-1, 1:-1, i])
        plt.colorbar()
    # plt.savefig()
    plt.show()
    pass

def load_data():
    num_node = 37
    # Purely thermal
    # data = sio.loadmat('2D_thermoelastic_36by36_xy_fixed_single_data5.mat')

    # purely structural
    #data = sio.loadmat('/home/hope-yao/Documents/MG_net/data/heat_transfer/Downloads/2D_thermoelastic_36by36_xy_fixed_single_data2.mat')

    # coupled loading
    data = sio.loadmat('2D_thermoelastic_36by36_xy_fixed_single_data4.mat')

    load = np.expand_dims(np.stack([-data['fx'], -data['fy'], data['ftem']], -1), 0).astype('float32')
    resp = np.expand_dims(np.stack([data['ux'], data['uy'], data['utem']], -1), 0).astype('float32')
    rho = [212e9, 0.288, 16., 12e-6] # E, mu, k, alpha

    train_load = load
    train_resp = resp
    test_load = load
    test_resp = resp
    data = {'num_node': num_node,
            'rho': rho,
            'train_load': train_load,
            'train_resp': train_resp,
            'test_load': test_load,
            'test_resp': test_resp,
            }
    return data

if __name__ == "__main__":
    import os
    os.environ['CUDA_DEVICE_ORDER'] = 'PCI_BUS_ID'
    os.environ['CUDA_VISIBLE_DEVICES'] = '1'
    cfg = {'lr': 100,
           'epoch': 100,
           }

    # load data
    data = load_data()

    # build the network
    model = FEA_Net_h(data,cfg)
    model.get_optimizer()
    model.initial_graph()

    # train the network
    res_hist = model.run_training(data)

    # visualize
    visualize(data, res_hist)
