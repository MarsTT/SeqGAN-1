import tensorflow as tf
import numpy as np
from tensorflow.python.ops import tensor_array_ops, control_flow_ops

class ROLLOUT(object):
    def __init__(self, lstm, update_rate):
        self.lstm = lstm
        self.update_rate = update_rate

        self.emb_num = self.lstm.emb_num
        self.batch_size = self.lstm.batch_size
        self.emb_dim = self.lstm.emb_dim
        self.hidden_dim = self.lstm.hidden_dim
        self.seq_len = self.lstm.seq_len
        self.start_token = tf.identity(self.lstm.start_token)
        self.lr = self.lstm.lr

        self.g_emb = tf.identity(self.lstm.g_emb)
        # maps h_tm1 to h_t for generator
        self.g_recurrent_unit = self.create_recurrent_unit()
        # maps h_t to o_t
        self.g_output_unit = self.create_output_unit()

        # Placeholders
        # sequence of tokens generated by generator
        self.x = tf.placeholder(tf.int32, shape=[self.batch_size, self.seq_len])
        self.given_num = tf.placeholder(tf.int32)

        # processed for batch
        with tf.device("/cpu:0"):
            # seq_length x batch_size x emb_dim
            self.processed_x = tf.transpose(tf.nn.embedding_lookup(self.g_emb, self.x), perm=[1, 0, 2])

        ta_emb_x = tensor_array_ops.TensorArray(dtype=tf.float32, size=self.seq_len)
        ta_emb_x = ta_emb_x.unstack(self.processed_x)

        ta_x = tensor_array_ops.TensorArray(dtype=tf.int32, size=self.seq_len)
        ta_x = ta_x.unstack(tf.transpose(self.x, perm=[1, 0]))

        self.h0 = tf.zeros([self.batch_size, self.hidden_dim])
        self.h0 = tf.stack([self.h0, self.h0])

        gen_x = tensor_array_ops.TensorArray(dtype=tf.int32, size=self.seq_len, dynamic_size=False, infer_shape=True)

        # When current index i < given_num, use the provided tokens as the input at each time step
        def g_recurrence_1(i, x_t, h_tm1, given_num, gen_x):
            # hidden_memory_tuple
            h_t = self.g_recurrent_unit(x_t, h_tm1)
            x_tp1 = ta_emb_x.read(i)
            gen_x = gen_x.write(i, ta_x.read(i))
            return i + 1, x_tp1, h_t, given_num, gen_x

        # When current index i >= given_num, start roll-out, use the output as time step t as the input at time step t+1
        def g_recurrence_2(i, x_t, h_tm1, given_num, gen_x):
            # hidden_memory_tuple
            h_t = self.g_recurrent_unit(x_t, h_tm1)
            # batch x vocab
            o_t = self.g_output_unit(h_t)
            log_prob = tf.log(tf.nn.softmax(o_t))
            next_token = tf.cast(tf.reshape(tf.multinomial(log_prob, 1), [self.batch_size]), tf.int32)
            # batch x emb_dim
            x_tp1 = tf.nn.embedding_lookup(self.g_emb, next_token)
            # indices, batch_size
            gen_x = gen_x.write(i, next_token)
            return i + 1, x_tp1, h_t, given_num, gen_x

        i, x_t, h_tm1, given_num, self.gen_x = control_flow_ops.while_loop(
            cond=lambda i, _1, _2, given_num, _4: i < given_num,
            body=g_recurrence_1,
            loop_vars=(tf.constant(0, dtype=tf.int32), tf.nn.embedding_lookup(self.g_emb, self.start_token), self.h0, self.given_num, gen_x))

        _, _, _, _, self.gen_x = control_flow_ops.while_loop(
            cond=lambda i, _1, _2, _3, _4: i < self.seq_len,
            body=g_recurrence_2,
            loop_vars=(i, x_t, h_tm1, given_num, self.gen_x))

        # seq_length x batch_size
        self.gen_x = self.gen_x.stack()
        # batch_size x seq_length
        self.gen_x = tf.transpose(self.gen_x, perm=[1, 0])

    def get_reward(self, sess, input_x, rollout_num, discriminator):
        rewards = []
        for i in range(rollout_num):
            # given_num between 1 to seq_len - 1 for a part completed sentence
            for given_num in range(1, self.seq_len ):
                feed = {self.x: input_x, self.given_num: given_num}
                samples = sess.run(self.gen_x, feed)
                feed = {discriminator.input_x: samples, discriminator.dropout_keep_prob: 1.0}
                ypred_for_auc = sess.run(discriminator.ypred_for_auc, feed)
                ypred = np.array([item[1] for item in ypred_for_auc])
                if i == 0:
                    rewards.append(ypred)
                else:
                    rewards[given_num - 1] += ypred

            # the last token reward
            feed = {discriminator.input_x: input_x, discriminator.dropout_keep_prob: 1.0}
            ypred_for_auc = sess.run(discriminator.ypred_for_auc, feed)
            ypred = np.array([item[1] for item in ypred_for_auc])
            if i == 0:
                rewards.append(ypred)
            else:
                # completed sentence reward
                rewards[self.seq_len - 1] += ypred

        # batch_size x seq_length
        rewards = np.transpose(np.array(rewards)) / (1.0 * rollout_num)
        return rewards

    def create_recurrent_unit(self):
        # Weights and Bias for input and hidden tensor
        self.W_i = tf.identity(self.lstm.W_i)
        self.U_i = tf.identity(self.lstm.U_i)
        self.b_i = tf.identity(self.lstm.b_i)

        self.W_f = tf.identity(self.lstm.W_f)
        self.U_f = tf.identity(self.lstm.U_f)
        self.b_f = tf.identity(self.lstm.b_f)

        self.W_o = tf.identity(self.lstm.W_o)
        self.U_o = tf.identity(self.lstm.U_o)
        self.b_o = tf.identity(self.lstm.b_o)

        self.W_c = tf.identity(self.lstm.W_c)
        self.U_c = tf.identity(self.lstm.U_c)
        self.b_c = tf.identity(self.lstm.b_c)

        def unit(x, hidden_memory_tm1):
            previous_hidden_state, c_prev = tf.unstack(hidden_memory_tm1)

            # Input Gate
            i = tf.sigmoid(
                tf.matmul(x, self.W_i) +
                tf.matmul(previous_hidden_state, self.U_i) + self.b_i
            )

            # Forget Gate
            f = tf.sigmoid(
                tf.matmul(x, self.W_f) +
                tf.matmul(previous_hidden_state, self.U_f) + self.b_f
            )

            # Output Gate
            o = tf.sigmoid(
                tf.matmul(x, self.W_o) +
                tf.matmul(previous_hidden_state, self.U_o) + self.b_o
            )

            # New Memory Cell
            c_ = tf.nn.tanh(
                tf.matmul(x, self.W_c) +
                tf.matmul(previous_hidden_state, self.U_c) + self.b_c
            )

            # Final Memory cell
            c = f * c_prev + i * c_

            # Current Hidden state
            current_hidden_state = o * tf.nn.tanh(c)

            return tf.stack([current_hidden_state, c])

        return unit

    def update_recurrent_unit(self):
        # Weights and Bias for input and hidden tensor
        self.W_i = self.update_rate * self.W_i + (1 - self.update_rate) * tf.identity(self.lstm.W_i)
        self.U_i = self.update_rate * self.U_i + (1 - self.update_rate) * tf.identity(self.lstm.U_i)
        self.b_i = self.update_rate * self.b_i + (1 - self.update_rate) * tf.identity(self.lstm.b_i)

        self.W_f = self.update_rate * self.W_f + (1 - self.update_rate) * tf.identity(self.lstm.W_f)
        self.U_f = self.update_rate * self.U_f + (1 - self.update_rate) * tf.identity(self.lstm.U_f)
        self.b_f = self.update_rate * self.b_f + (1 - self.update_rate) * tf.identity(self.lstm.b_f)

        self.W_o = self.update_rate * self.W_o + (1 - self.update_rate) * tf.identity(self.lstm.W_o)
        self.U_o = self.update_rate * self.U_o + (1 - self.update_rate) * tf.identity(self.lstm.U_o)
        self.b_o = self.update_rate * self.b_o + (1 - self.update_rate) * tf.identity(self.lstm.b_o)

        self.W_c = self.update_rate * self.W_c + (1 - self.update_rate) * tf.identity(self.lstm.W_c)
        self.U_c = self.update_rate * self.U_c + (1 - self.update_rate) * tf.identity(self.lstm.U_c)
        self.b_c = self.update_rate * self.b_c + (1 - self.update_rate) * tf.identity(self.lstm.b_c)

        def unit(x, hidden_memory_tm1):
            previous_hidden_state, c_prev = tf.unstack(hidden_memory_tm1)

            # Input Gate
            i = tf.sigmoid(
                tf.matmul(x, self.W_i) +
                tf.matmul(previous_hidden_state, self.U_i) + self.b_i
            )

            # Forget Gate
            f = tf.sigmoid(
                tf.matmul(x, self.W_f) +
                tf.matmul(previous_hidden_state, self.U_f) + self.b_f
            )

            # Output Gate
            o = tf.sigmoid(
                tf.matmul(x, self.W_o) +
                tf.matmul(previous_hidden_state, self.U_o) + self.b_o
            )

            # New Memory Cell
            c_ = tf.nn.tanh(
                tf.matmul(x, self.W_c) +
                tf.matmul(previous_hidden_state, self.U_c) + self.b_c
            )

            # Final Memory cell
            c = f * c_prev + i * c_

            # Current Hidden state
            current_hidden_state = o * tf.nn.tanh(c)

            return tf.stack([current_hidden_state, c])

        return unit

    def create_output_unit(self):
        self.Wo = tf.identity(self.lstm.Wo)
        self.bo = tf.identity(self.lstm.bo)

        def unit(hidden_memory_tuple):
            hidden_state, c_prev = tf.unstack(hidden_memory_tuple)
            # hidden_state : batch x hidden_dim
            logits = tf.matmul(hidden_state, self.Wo) + self.bo
            # output = tf.nn.softmax(logits)
            return logits

        return unit

    def update_output_unit(self):
        self.Wo = self.update_rate * self.Wo + (1 - self.update_rate) * tf.identity(self.lstm.Wo)
        self.bo = self.update_rate * self.bo + (1 - self.update_rate) * tf.identity(self.lstm.bo)

        def unit(hidden_memory_tuple):
            hidden_state, c_prev = tf.unstack(hidden_memory_tuple)
            # hidden_state : batch x hidden_dim
            logits = tf.matmul(hidden_state, self.Wo) + self.bo
            # output = tf.nn.softmax(logits)
            return logits

        return unit

    def update_params(self):
        self.g_emb = tf.identity(self.lstm.g_emb)
        self.g_recurrent_unit = self.update_recurrent_unit()
        self.g_output_unit = self.update_output_unit()