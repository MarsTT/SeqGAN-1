# Aims of this file:
# Image to text mapping, text vocabulary, text_to_idx, idx_to_text

import operator
import re
import os
import json
import logging
from collections import Counter

from tqdm import tqdm
import colorlog
from sklearn.feature_extraction.text import TfidfTransformer
import numpy as np

# Hyperparameters
CONTEXT_LENGTH = 100
CAPTION_VOCAB_SIZE = 100000
HASHTAG_VOCAB_SIZE = 60000
DATA_ROOT_PATH = 'instapic'

# For dataset
CAPTION_TRAIN_JSON_FNAME = os.path.join(
    DATA_ROOT_PATH, 'json', 'insta-caption-train.json'
)
CAPTION_TEST1_JSON_FNAME = os.path.join(
    DATA_ROOT_PATH, 'json', 'insta-caption-test1.json'
)
CAPTION_TEST2_JSON_FNAME = os.path.join(
    DATA_ROOT_PATH, 'json', 'insta-caption-test2.json'
)
HASHTAG_TRAIN_JSON_FNAME = os.path.join(
    DATA_ROOT_PATH, 'json', 'insta-hashtag-train.json'
)
HASHTAG_TEST1_JSON_FNAME = os.path.join(
    DATA_ROOT_PATH, 'json', 'insta-hashtag-test1.json'
)
HASHTAG_TEST2_JSON_FNAME = os.path.join(
    DATA_ROOT_PATH, 'json', 'insta-hashtag-test2.json'
)

CAPTION_OUTPUT_PATH = os.path.join(DATA_ROOT_PATH, 'caption_dataset')
HASHTAG_OUTPUT_PATH = os.path.join(DATA_ROOT_PATH, 'hashtag_dataset')

CAPTION_VOCAB_FNAME = os.path.join(
    CAPTION_OUTPUT_PATH, '%d.vocab' % (CAPTION_VOCAB_SIZE)
)
HASHTAG_VOCAB_FNAME = os.path.join(
    HASHTAG_OUTPUT_PATH, '%d.vocab' % (HASHTAG_VOCAB_SIZE)
)

# For vocaulary
_PAD = "_pad"
_GO = "_go"
_EOS = "_eos"
_UNK = "_unk"
_START_VOCAB = [_PAD, _GO, _EOS, _UNK]

PAD_ID = 0
GO_ID = 1
EOS_ID = 2
UNK_ID = 3

# For tokenization
try:
    # UCS-4
    EMOTICON = re.compile('(([\U00002600-\U000027BF])|([\U0001f300-\U0001f64F])|([\U0001f680-\U0001f6FF]))')
except Exception as e:
    # UCS-2
    EMOTICON = re.compile('(([\u2600-\u27BF])|([\uD83C][\uDF00-\uDFFF])|([\uD83D][\uDC00-\uDE4F])|([\uD83D][\uDE80-\uDEFF]))')
NOT_EMOTICON = re.compile(r'(\\U([0-9A-Fa-f]){8})|(\\u([0-9A-Fa-f]){4})')

def sort_dict(dic):
    # Sort by alphabet
    sorted_pair_list = sorted(list(dic.items()), key=operator.itemgetter(0))
    # Sort by count
    sorted_pair_list = sorted(sorted_pair_list, key=operator.itemgetter(1), reverse=True)
    return sorted_pair_list

def load_json(json_fname):
    colorlog.info("Load %s" % (json_fname))
    with open(json_fname, 'r') as f:
        json_object = json.load(f)
    return json_object

def tokenize(sentence):
    if isinstance(sentence, list):
        sentence = ' '.join(sentence)

    sentence = sentence.replace('#', ' #')
    sentence = sentence.replace('@', ' @')
    sentence = sentence.replace('\n', ' ')
    sentence = sentence.lower()
    sentence = re.sub(r"@[a-zA-Z0-9._]+", "@username", sentence)  # change username
    sentence = EMOTICON.sub(r"@@byeongchang\1 ", sentence)
    # sentence = sentence.encode('unicode-escape')  # for emoticons
    sentence = re.sub(r'@@byeongchang\\', '@@byeongchang', sentence)
    sentence = NOT_EMOTICON.sub(r' ', sentence)
    sentence = re.sub(r"[\-_]", r"-", sentence)  # incoporate - and _
    sentence = re.sub(r"([!?,\.\"])", r" ", sentence)  # remove duplicates on . , ! ?
    sentence = re.sub(r"(?<![a-zA-Z0-9])\-(?![a-zA-Z0-9])", r"", sentence)  # remove - if there is no preceed or following
    sentence = ' '.join(re.split(r'[^a-zA-Z0-9#@\'\-]+', sentence))
    sentence = re.sub(r'@@byeongchang', r' \\', sentence)
    return sentence.split()

def tokenize_all(train_json, test1_json, test2_json, key='caption'):
    """
    Tokenize sentences in raw dataset
    Args:
    train_json, test1_json, test2_json: raw json object
    key: 'caption' or 'tags'
    """

    colorlog.info("Tokenize %s data" % (key))
    token_counter = Counter()
    train_tokens = {}
    test1_tokens = {}
    test2_tokens = {}

    # Train data
    for user_id, posts in tqdm(list(train_json.items()), ncols=70, desc="train data"):
        train_tokens[user_id] = {}
        for post_id, post in list(posts.items()):
            post_tokens = tokenize(post[key])
            train_tokens[user_id][post_id] = post_tokens
            for post_token in post_tokens:
                token_counter[post_token] += 1

    # Test1 data
    for user_id, posts in tqdm(list(test1_json.items()), ncols=70, desc="test1 data"):
        test1_tokens[user_id] = {}
        for post_id, post in list(posts.items()):
            post_tokens = tokenize(post[key])
            test1_tokens[user_id][post_id] = post_tokens

    # Test2 data
    for user_id, posts in tqdm(list(test2_json.items()), ncols=70, desc="test2 data"):
        test2_tokens[user_id] = {}
        for post_id, post in list(posts.items()):
            post_tokens = tokenize(post[key])
            test2_tokens[user_id][post_id] = post_tokens

    return token_counter, train_tokens, test1_tokens, test2_tokens


def get_tfidf_words(train_tokens, test1_tokens, test2_tokens, vocab, rev_vocab):
    colorlog.info("Get tfidf words")
    def _preprocess(all_tokens, rev_vocab):
        counter = np.zeros([len(all_tokens), len(rev_vocab)])
        user_ids = []
        for i, (user_id, posts) in enumerate(tqdm(list(all_tokens.items()), ncols=70, desc="preprocess")):
            user_ids.append(user_id)
            for post_id, tokens in list(posts.items()):
                token_ids = [rev_vocab.get(token, UNK_ID) for token in tokens]
                for token_id in token_ids:
                    counter[i, token_id] += 1
        return counter, user_ids

    train_counter, train_user_ids = _preprocess(train_tokens, rev_vocab)
    test1_counter, test1_user_ids = _preprocess(test1_tokens, rev_vocab)
    test2_counter, test2_user_ids = _preprocess(test2_tokens, rev_vocab)

    colorlog.info("Fit and transform train tfidf")
    vectorizer = TfidfTransformer()
    train_tfidf = vectorizer.fit_transform(train_counter).toarray()
    test1_tfidf = vectorizer.transform(test1_counter).toarray()
    test2_tfidf = vectorizer.transform(test2_counter).toarray()

    def _extract_tokens(tfidfs, user_ids, vocab):
        user_tokens = {}
        for i, user_id in enumerate(user_ids):
            tfidf = np.argsort(-tfidfs[i])[:CONTEXT_LENGTH]
            weight = np.sort(-tfidfs[i])[:CONTEXT_LENGTH]
            tokens = []
            for j, (index, token_weight) in enumerate(zip(tfidf, weight)):
                token = vocab[index]
                if token_weight < 0.0:
                    if index != UNK_ID:
                        tokens.append(token)
                else:
                    break
            user_tokens[user_id] = tokens
        return user_tokens

    colorlog.info("Extract tokens from tfidf matrix")
    train_user_tokens = _extract_tokens(train_tfidf, train_user_ids, vocab)
    test1_user_tokens = _extract_tokens(test1_tfidf, test1_user_ids, vocab)
    test2_user_tokens = _extract_tokens(test2_tfidf, test2_user_ids, vocab)

    return train_user_tokens, test1_user_tokens, test2_user_tokens

def create_vocabulary(counter, fname, vocab_size):
    colorlog.info("Create vocabulary %s" % (fname))
    sorted_tokens = sort_dict(counter)
    vocab = _START_VOCAB + [x[0] for x in sorted_tokens]
    # if len(vocab) > vocab_size:
    #     vocab = vocab[:vocab_size]
    with open(fname, 'w') as f:
        for w in vocab:
            f.write(w + "\n")

    rev_vocab = {}
    for i, token in enumerate(vocab):
        rev_vocab[token] = i

    return vocab, rev_vocab

def save_data(train_data, test1_data, test2_data, output_path, rev_vocab):
    """
    Data format:
    numpyfname,contextlength,captionlength,contexttoken1_contexttoken2,wordtoken1_wordtoken2
    e.g. 12345.npy,4,3,445_24_445_232,134_466_234
    """
    def _save_data(all_tokens, all_tfidf, fname):
        all_strings = []
        for user_id, posts in list(all_tokens.items()):
            context_tokenids = list(map(str, [rev_vocab.get(token, UNK_ID) for token in all_tfidf[user_id]]))
            context_length = str(len(context_tokenids))
            context_string = '_'.join(context_tokenids)
            for post_id, tokens in list(posts.items()):
                caption_tokenids = list(map(str, [rev_vocab.get(token, UNK_ID) for token in tokens]))
                caption_length = str(len(caption_tokenids))
                caption_string = '_'.join(caption_tokenids)
                numpy_string = '%s_@_%s.npy' % (user_id, post_id)

                all_string = ','.join([numpy_string, context_length, caption_length, context_string, caption_string])
                all_strings.append((all_string + '\n', len(caption_tokenids)))

        # sort by caption length
        all_strings = sorted(all_strings, key=lambda x: x[1])

        with open(fname, 'w') as f:
            for all_string in all_strings:
                f.write(all_string[0])

    _save_data(
    train_data[0], train_data[1], os.path.join(output_path, "train.txt")
    )
    _save_data(
    test1_data[0], test1_data[1], os.path.join(output_path, "test1.txt")
    )
    _save_data(
    test2_data[0], test2_data[1], os.path.join(output_path, "test2.txt")
    )

def main():
    colorlog.basicConfig(
      filename=None,
      level=logging.INFO,
      format="%(log_color)s[%(levelname)s:%(asctime)s]%(reset)s %(message)s",
    )

    if not os.path.exists(CAPTION_OUTPUT_PATH):
        colorlog.info("Create directory %s" % (CAPTION_OUTPUT_PATH))
        os.makedirs(CAPTION_OUTPUT_PATH)
    if not os.path.exists(HASHTAG_OUTPUT_PATH):
        colorlog.info("Create directory %s" % (HASHTAG_OUTPUT_PATH))
        os.makedirs(HASHTAG_OUTPUT_PATH)

    # Load raw data
    caption_train_json = load_json(CAPTION_TRAIN_JSON_FNAME)
    caption_test1_json = load_json(CAPTION_TEST1_JSON_FNAME)
    caption_test2_json = load_json(CAPTION_TEST2_JSON_FNAME)
    hashtag_train_json = load_json(HASHTAG_TRAIN_JSON_FNAME)
    hashtag_test1_json = load_json(HASHTAG_TEST1_JSON_FNAME)
    hashtag_test2_json = load_json(HASHTAG_TEST2_JSON_FNAME)

    # Tokenize all
    caption_counter, caption_train_tokens, caption_test1_tokens, caption_test2_tokens = tokenize_all(
                caption_train_json,
                caption_test1_json,
                caption_test2_json,
                'caption'
                )
    hashtag_counter, hashtag_train_tokens, hashtag_test1_tokens, hashtag_test2_tokens = tokenize_all(
                hashtag_train_json,
                hashtag_test1_json,
                hashtag_test2_json,
                'tags'
                )

    # Create vocabulary
    caption_vocab, caption_rev_vocab = create_vocabulary(caption_counter, CAPTION_VOCAB_FNAME, CAPTION_VOCAB_SIZE)
    hashtag_vocab, hashtag_rev_vocab = create_vocabulary(hashtag_counter, HASHTAG_VOCAB_FNAME, HASHTAG_VOCAB_SIZE)

    # Get tfidf weighted tokens
    # caption_train_tfidf_tokens, caption_test1_tfidf_tokens, caption_test2_tfidf_tokens = get_tfidf_words(
    #                 caption_train_tokens,
    #                 caption_test1_tokens,
    #                 caption_test2_tokens,
    #                 caption_vocab,
    #                 caption_rev_vocab
    #                 )
    # hashtag_train_tfidf_tokens, hashtag_test1_tfidf_tokens, hashtag_test2_tfidf_tokens = get_tfidf_words(
    #                 hashtag_train_tokens,
    #                 hashtag_test1_tokens,
    #                 hashtag_test2_tokens,
    #                 hashtag_vocab,
    #                 hashtag_rev_vocab
    #                 )
    #
    # # Save data
    # save_data(
    #             (caption_train_tokens, caption_train_tfidf_tokens),
    #             (caption_test1_tokens, caption_test1_tfidf_tokens),
    #             (caption_test2_tokens, caption_test2_tfidf_tokens),
    #             CAPTION_OUTPUT_PATH,
    #             caption_rev_vocab
    #         )
    # save_data(
    #             (hashtag_train_tokens, hashtag_train_tfidf_tokens),
    #             (hashtag_test1_tokens, hashtag_test1_tfidf_tokens),
    #             (hashtag_test2_tokens, hashtag_test2_tfidf_tokens),
    #             HASHTAG_OUTPUT_PATH,
    #             hashtag_rev_vocab
    #         )

if __name__ == '__main__':
    main()
