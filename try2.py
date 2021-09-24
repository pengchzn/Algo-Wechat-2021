from tqdm import tqdm

tqdm.pandas(desc='pandas bar')
import gc
import numpy as np
from evaluation import uAUC
import pandas as pd
import lightgbm as lgb
from lightgbm import LGBMRegressor
from sklearn.model_selection import GridSearchCV
from sklearn.model_selection import train_test_split
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.preprocessing import OneHotEncoder, LabelEncoder
from scipy import sparse
from hyperopt import fmin,tpe,hp,partial
from sklearn.metrics import mean_squared_error


def reduce_mem(df):
    start_mem = df.memory_usage().sum() / 1024 ** 2
    for col in df.columns:
        col_type = df[col].dtypes
        if col_type != object:
            c_min = df[col].min()
            c_max = df[col].max()
            if str(col_type)[:3] == 'int':
                if c_min > np.iinfo(np.int8).min and c_max < np.iinfo(np.int8).max:
                    df[col] = df[col].astype(np.int8)
                elif c_min > np.iinfo(np.int16).min and c_max < np.iinfo(np.int16).max:
                    df[col] = df[col].astype(np.int16)
                elif c_min > np.iinfo(np.int32).min and c_max < np.iinfo(np.int32).max:
                    df[col] = df[col].astype(np.int32)
                elif c_min > np.iinfo(np.int64).min and c_max < np.iinfo(np.int64).max:
                    df[col] = df[col].astype(np.int64)
            else:
                if c_min > np.finfo(np.float16).min and c_max < np.finfo(np.float16).max:
                    df[col] = df[col].astype(np.float16)
                elif c_min > np.finfo(np.float32).min and c_max < np.finfo(np.float32).max:
                    df[col] = df[col].astype(np.float32)
                else:
                    df[col] = df[col].astype(np.float64)
    end_mem = df.memory_usage().sum() / 1024 ** 2
    print('{:.2f} Mb, {:.2f} Mb ({:.2f} %)'.format(start_mem, end_mem, 100 * (start_mem - end_mem) / start_mem))
    gc.collect()
    return df


path = './wechat_algo_data1/'
train = pd.read_csv(path + 'user_action.csv')
test = pd.read_csv(path + 'test_a.csv')
feed_info = pd.read_csv(path + 'feed_info.csv')
res = test[['userid', 'feedid']]
feed_info = feed_info.fillna('')

feed_one_hot_feature = ['feedid', 'authorid', 'videoplayseconds', 'bgm_song_id', 'bgm_singer_id']
# feed_vector_feature = [description'', 'ocr', 'asr','machine_keyword_list', 'manual_keyword_list', 'manual_tag_list', 'machine_tag_list','description_char', 'ocr_char', 'asr_char']
feed_vector_feature = ['description']

# 替换分隔符
# for col in feed_vector_feature:
#     feed_info[col] = feed_info[col].apply(lambda x:str(x).replace(';',' '))

train = pd.merge(train, feed_info, on=['feedid'], how='left', copy=False)
test = pd.merge(test, feed_info, on=['feedid'], how='left', copy=False)

test['date_'] = 15

data = pd.concat([train, test], axis=0, copy=False)

label_columns = ['read_comment', 'comment', 'like', 'click_avatar', 'forward', 'follow', 'favorite', ]
drop_columns = ['play', 'stay', 'date_']
action_one_hot_feature = ['userid', 'device']

one_hot_feature = feed_one_hot_feature + action_one_hot_feature

for feature in one_hot_feature:
    data[feature] = LabelEncoder().fit_transform(data[feature].apply(str))

train_shape = data[data['date_'] < 14].shape[0]
valid_shape = data[data['date_'] == 14].shape[0]

train = data[data['date_'] < 15]
train_y = data[data['date_'] < 15][label_columns]

x_train_shape = train[train['date_'] != 14].shape[0]

test = data[data['date_'] == 15]
test_y = data[data['date_'] == 15][label_columns]

enc = OneHotEncoder()
for index, feature in enumerate(one_hot_feature):
    print(feature)
    enc.fit(data[feature].values.reshape(-1, 1))
    train_a = enc.transform(train[feature].values.reshape(-1, 1))
    test_a = enc.transform(test[feature].values.reshape(-1, 1))
    test_b = enc.transform(test[feature].values.reshape(-1, 1))
    if index == 0:
        train_x = train_a
        test_x = test_a
        test_y = test_b
        print(train_x)
    else:
        train_x = sparse.hstack((train_x, train_a))
        test_x = sparse.hstack((test_x, test_a))
        test_y = sparse.hstack(test_y,test_b)
print('one-hot prepared !')

train = train_x.tocsr()
X_test = test_x.tocsr()
y_test = test_y.tocsr()

X_train = train[:train_shape]
X_valid = train[train_shape:]

print(X_train.shape, X_valid.shape, X_test.shape)

y_train = train_y[:train_shape]
y_valid = train_y[train_shape:]
print(y_train.shape, y_valid.shape)

# 自定义hyperopt的参数空间
space = {"max_depth": hp.randint("max_depth", 15),
         "num_trees": hp.randint("num_trees", 300),
         'learning_rate': hp.uniform('learning_rate', 1e-3, 5e-1),
         "bagging_fraction": hp.randint("bagging_fraction", 5),
         "num_leaves": hp.randint("num_leaves", 6),
         }

def argsDict_tranform(argsDict, isPrint=False):
    argsDict["max_depth"] = argsDict["max_depth"] + 5
    argsDict['num_trees'] = argsDict['num_trees'] + 150
    argsDict["learning_rate"] = argsDict["learning_rate"] * 0.02 + 0.05
    argsDict["bagging_fraction"] = argsDict["bagging_fraction"] * 0.1 + 0.5
    argsDict["num_leaves"] = argsDict["num_leaves"] * 3 + 10
    if isPrint:
        print(argsDict)
    else:
        pass

    return argsDict

def lightgbm_factory(argsDict):
    argsDict = argsDict_tranform(argsDict)
    auc_sum = 0
    bst_item = []
    ul = data[data['date_'] == 14]['userid'].tolist()
    weight_label = [4, 3, 2, 1]
    params = {'nthread': -1,  # 进程数
              'max_depth': argsDict['max_depth'],  # 最大深度
              'num_trees': argsDict['num_trees'],  # 树的数量
              'eta': argsDict['learning_rate'],  # 学习率
              'bagging_fraction': argsDict['bagging_fraction'],  # bagging采样数
              'num_leaves': argsDict['num_leaves'],  # 终点节点最小样本占比的和
              'feature_fraction': 0.7,  # 样本列采样
              'lambda_l1': 0,  # L1 正则化
              'lambda_l2': 0,  # L2 正则化
              'bagging_seed': 100,  # 随机种子,light中默认为100
              'boosting_type': 'gbdt',
              'objective': 'binary',
              'metric': 'auc',
              'learning_rate': 0.05,
              'bagging_freq': 5,
              'verbose': 0,
              'random_state': 45,
              'n_jobs': -1,
              'min_child_samples': 21,
              'min_child_weight': 0.001,
              'reg_alpha': 0.001,
              'reg_lambda': 8,
              'cat_smooth': 0,
              'force_col_wise': True,
              # 'two_round': True
              }
    #rmse
    params['metric'] = ['rmse']

    for index, label in enumerate(['read_comment', 'like', 'click_avatar', 'forward']):
        print(label, index)
        dtrain = lgb.Dataset(X_train, label=y_train[label].values)
        dval = lgb.Dataset(X_valid, label=y_valid[label].values)
        lgb_model = lgb.train(params, dtrain, num_boost_round=300, valid_sets=[dval], early_stopping_rounds=100)
        X_valid_pred = lgb_model.predict(X_valid, num_iteration=lgb_model.best_iteration)
        bst_item.append(lgb_model.best_iteration)
        v = uAUC(y_valid[label].tolist(), X_valid_pred.tolist(), ul)
        print(label, v)
        auc_sum = auc_sum + weight_label[index] * v / np.sum(weight_label)

    print(auc_sum)



    return get_tranformer_score(lgb_model)

def get_tranformer_score(tranformer):

    model = tranformer
    prediction = model.predict(X_test, num_iteration=model.best_iteration)

    return mean_squared_error(y_test, prediction)

# 开始使用hyperopt进行自动调参
algo = partial(tpe.suggest, n_startup_jobs=1)
best = fmin(lightgbm_factory, space, algo=algo, max_evals=20, pass_expr_memo_ctrl=None)

RMSE = lightgbm_factory(best)
print('best :', best)
print('best param after transform :')
argsDict_tranform(best,isPrint=True)
print('rmse of the best lightgbm:', np.sqrt(RMSE))