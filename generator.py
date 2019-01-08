'''
Standard LSTM is employed as the building block of the generator policy.
The generative model has been regarded as a stochastic parameterized policy which uses Monte Carlo search to approximate state-action value. Then, they train the policy via policy gradient which avoids the differentiation difficulty for discrete data in a conventional GAN.
'''
import tensorflow as tf
import numpy as np
# Get to know use of these functions
from tensorflow.python.ops import tensor_array_ops, control_flow_ops

class Generator(object):
    def __init__(self, emb_num, batch_size, emb_dim, hidden_dim, seq_len, start_token, lr=0.01, reward_gamma=0.95):
        self.emb_num = emb_num
        self.batch_size = batch_size
        self.emb_dim = emb_dim
        self.hidden_dim = hidden_dim
        self.seq_len = seq_len
        self.start_token = tf.constant([start_token]*self.batch_size, dtype=tf.int32)
        self.lr = tf.Variable(float(lr), trainable=False)
        self.reward_gamma = reward_gamma
        self.g_params = []
        self.d_params = []
        # What are these variables for?
        self.temperature = 1.0
        self.grad_clip = 5.0

        self.expected_reward = tf.Variable(tf.zeros([self.seq_len]))

        # LSTM Generator
        with tf.variable_scope("generator"):
            self.g_emb = tf.Variable(tf.random_normal([self.emb_num, self.emb_dim]))
            self.g_params.append(self.g_emb)
            # Maps h_tm1 to h_t for generator. h_tm1 means hidden layer at t-1 step
            self.g_recurrent_unit = self.create_lstm_unit(self.g_params)
            # Mapt h_t to o_t (token logits)
            self.g_output_unit = self.create_output_unit(self.g_params)

        # Placeholders
        # Sequence of tokens generated by generator
        self.x = tf.placeholder(tf.int32, shape=[self.batch_size, self.seq_len])
        # Rewards will come from rollout policy and discriminator as discussed in paper
        self.rewards = tf.placeholder(tf.float32, shape=[self.batch_size, self.seq_len])

        # Processed for batch
        with tf.device("/cpu:0"):
            # Seq_len x batch_size x emb_dim for LSTM cell
            self.processed_x = tf.transpose(tf.nn.embedding_lookup(self.g_emb, self.x), perm=[1, 0, 2])

        # Initial states
        self.h0 = tf.zeros([self.batch_size, self.hidden_dim])
        self.h0 = tf.stack([self.h0, self.h0])

        # gen_o is in float because it stores probability, gen_x stores token values which are integers
        gen_o = tensor_array_ops.TensorArray(dtype=tf.float32, size=self.seq_len, dynamic_size=False, infer_shape=True)
        gen_x = tensor_array_ops.TensorArray(dtype=tf.int32, size=self.seq_len, dynamic_size=False, infer_shape=True)

        # i - indices, x_t is current state, h_tm1 is previous hidden state
        def g_recurrence(i, x_t, h_tm1, gen_o, gen_x):
            # hidden memory tuple
            h_t = self.g_recurrent_unit(x_t, h_tm1)
            # batch x vocabulary
            o_t = self.g_output_unit(h_t)
            log_prob = tf.log(tf.nn.softmax(o_t))
            # Generates next sequence of data through multinomial distribution and casts to integer values
            next_token = tf.cast(tf.reshape(tf.multinomial(log_prob, 1), [self.batch_size]), tf.int32)
            # Embeddings for next token (batch x emb_dim)
            x_tp1 = tf.nn.embedding_lookup(self.g_emb, next_token)
            # Save probability of the select token ([batch_size])
            gen_o = gen_o.write(i, tf.reduce_sum(tf.multiply(tf.one_hot(next_token, self.emb_num, 1.0, 0.0), tf.nn.softmax(o_t)), 1))
            # Save token generated - indices, batch_size
            gen_x = gen_x.write(i, next_token)
            return i+1, x_tp1, h_t, gen_o, gen_x

        # While looping the function g_recurrence
        _, _, _, self.gen_o, self.gen_x = control_flow_ops.while_loop(
            cond=lambda i, _1, _2, _3, _4: i < self.seq_len,
            body=g_recurrence,
            loop_vars=(tf.constant(0, dtype=tf.int32), tf.nn.embedding_lookup(self.g_emb, self.start_token), self.h0, gen_o, gen_x)
        )

        # seq_length x batch_size
        self.gen_x = self.gen_x.stack()
        # batch_size x seq_length
        self.gen_x = tf.transpose(self.gen_x, perm=[1, 0])

        # Supervised pretraining for generator
        g_pred = tensor_array_ops.TensorArray(dtype=tf.float32, size=self.seq_len, dynamic_size=False, infer_shape=True)
        ta_emb_x = tensor_array_ops.TensorArray(dtype=tf.float32, size=self.seq_len)
        # embedded x : seq * batch_size *  emb_size
        ta_emb_x = ta_emb_x.unstack(self.processed_x)

        def pretrain_recurrence(i, x_t, h_tm1, g_pred):
            h_t = self.g_recurrent_unit(x_t, h_tm1)
            o_t = self.g_output_unit(h_t)
            # batch x vocabulary_size
            g_pred = g_pred.write(i, tf.nn.softmax(o_t))
            x_tp1 = ta_emb_x.read(i)
            return i+1, x_tp1, h_t, g_pred

        _, _, _, self.g_pred = control_flow_ops.while_loop(
            cond=lambda i, _1, _2, _3, _4: i < self.seq_len,
            body=pretrain_recurrence,
            loop_vars=(tf.constant(0, dtype=tf.int32), tf.nn.embedding_lookup(self.g_emb, self.start_token), self.h0, g_pred)
        )
        # batch_size x seq_length x vocab_size
        self.g_pred = tf.transpose(self.g_pred, perm=[1, 0, 2])

        # pretraining loss
        self.pretrain_loss = -tf.reduce_sum(tf.one_hot(tf.to_int32(tf.reshape(self.x, [-1])), self.emb_num, 1.0, 0.0) * tf.log(tf.clip_by_value(tf.reshape(self.g_pred, [-1, self.emb_num]), 1e-20, 1.0)))/(self.seq_len*self.batch_size)

        # training updates
        pretrain_opt = self.g_optimizer(self.lr)

        self.pretrain_grad, _ = tf.clip_by_global_norm(tf.gradients(self.pretrain_loss, self.g_params), self.grad_clip)
        self.pretrain_updates = pretrain_opt.apply_gradients(zip(self.pretrain_grad, self.g_params))

        # UNSUPERVISED LEARNING
        self.g_loss = -tf.reduce_sum(tf.reduce_sum(tf.one_hot(tf.to_int32(tf.reshape(self.x, [-1])), self.emb_num, 1.0, 0.0) * tf.log(tf.clip_by_value(tf.reshape(self.g_pred, [-1, self.emb_num]), 1e-20, 1.0)), 1) * tf.reshape(self.rewards, [-1]))

        g_opt = self.g_optimizer(self.lr)

        self.g_grad, _ = tf.clip_by_global_norm(tf.gradients(self.g_loss, self.g_params), self.grad_clip)
        self.g_updates = g_opt.apply_gradients(zip(self.g_grad, self.g_params))

    def generate(self, sess):
        outputs = sess.run(self.gen_x)
        return outputs

    def pretrain_step(self, sess, x):
        outputs = sess.run([self.pretrain_updates, self.pretrain_loss], feed_dict={self.x: x})
        return outputs

    def init_matrix(self, shape):
        return tf.random_normal(shape, stddev=0.1)

    def init_vector(self, shape):
        return tf.zeros(shape)

    def create_lstm_unit(self, params):
        # Weights and bias for input, forget, output and carry gates
        self.W_i = tf.Variable(self.init_matrix([self.emb_dim, self.hidden_dim]))
        self.U_i = tf.Variable(self.init_matrix([self.hidden_dim, self.hidden_dim]))
        self.b_i = tf.Variable(self.init_matrix([self.hidden_dim]))

        self.W_f = tf.Variable(self.init_matrix([self.emb_dim, self.hidden_dim]))
        self.U_f = tf.Variable(self.init_matrix([self.hidden_dim, self.hidden_dim]))
        self.b_f = tf.Variable(self.init_matrix([self.hidden_dim]))

        self.W_o = tf.Variable(self.init_matrix([self.emb_dim, self.hidden_dim]))
        self.U_o = tf.Variable(self.init_matrix([self.hidden_dim, self.hidden_dim]))
        self.b_o = tf.Variable(self.init_matrix([self.hidden_dim]))

        self.W_c = tf.Variable(self.init_matrix([self.emb_dim, self.hidden_dim]))
        self.U_c = tf.Variable(self.init_matrix([self.hidden_dim, self.hidden_dim]))
        self.b_c = tf.Variable(self.init_matrix([self.hidden_dim]))

        params.extend([
                        self.W_i, self.U_i, self.b_i,
                        self.W_f, self.U_f, self.b_f,
                        self.W_o, self.U_o, self.b_o,
                        self.W_c, self.U_c, self.b_c])

        def unit(x, hidden_mem_tm1):
            prev_hidden_state, c_prev = tf.unstack(hidden_mem_tm1)
            # Input gate
            i = tf.sigmoid(tf.matmul(x, self.W_i) + tf.matmul(prev_hidden_state, self.U_i) + self.b_i)
            # Forget Gate
            f = tf.sigmoid(tf.matmul(x, self.W_f) + tf.matmul(prev_hidden_state, self.U_f) + self.b_f)
            # Output Gate
            o = tf.sigmoid(tf.matmul(x, self.W_o) + tf.matmul(prev_hidden_state, self.U_o) + self.b_o)
            # New Memory Cell
            c_ = tf.nn.tanh(tf.matmul(x, self.W_c) + tf.matmul(prev_hidden_state, self.U_c) + self.b_c)
            # Final Memory cell
            c = f*c_prev + i*c_
            # Current Hidden state
            current_hidden_state = o * tf.nn.tanh(c)

            return tf.stack([current_hidden_state, c])

        return unit

    def create_output_unit(self, params):
        self.Wo = tf.Variable(self.init_matrix([self.hidden_dim, self.emb_num]))
        self.bo = tf.Variable(self.init_matrix([self.emb_num]))
        params.extend([self.Wo, self.bo])

        def unit(hidden_mem_tuple):
            hidden_state, c_prev = tf.unstack(hidden_mem_tuple)
            # hidden_state: batch x hidden_dim
            logits = tf.matmul(hidden_state, self.Wo) + self.bo
            return logits

        return unit

    def g_optimizer(self, *args, **kwargs):
        return tf.train.AdamOptimizer(*args, **kwargs)
