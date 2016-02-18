from keras.models import Sequential
from keras.layers.core import Dense, Dropout
from keras.optimizers import Adadelta
from keras.regularizers import l1
from keras.callbacks import EarlyStopping
from sklearn.cross_validation import StratifiedShuffleSplit
from sklearn.metrics import roc_auc_score, f1_score
from sklearn.preprocessing import LabelEncoder
from sklearn.base import BaseEstimator, ClassifierMixin
from data_generator import DataGeneratorByGroup as dbg
from theano import function
import numpy as np
import logging

logging.basicConfig(format="[%(module)s:%(levelname)s]:%(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class BaseMLP(BaseEstimator, ClassifierMixin):
    '''
    Model class that wraps Keras model

    Input:
    in_dim: number of variables of the data
    out_dim: number of classes to predict
    n_hidden: number of hidden variables at each layers
    n_deep: number of layers
    l1_norm: penalization coefficient for L1 norm on hidden variables
    drop: dropout percentage at each layer
    verbose: verbosity level (up to 3 levels)

    Methods:
    reset_weigths: re-initiallizes the weights
    save/load: saves/loads weights to a file
    fit: trains on data provided with early stopping
    train_batch: trains on batch provided splitting
                 the data into train and validation
    fit_batches: trains on a sequence of batches with early stopping
    predict: returns prediction on data provided
    auc: returns area under the roc curve on data and true
         labels provided
    '''
    def __init__(self, n_hidden=1000, n_deep=4,
                 l1_norm=0.01, drop=0.1,
                 early_stop=True, max_epoch=5000,
                 patience=200,
                 learning_rate=1, verbose=2):
        self.max_epoch = max_epoch
        self.early_stop = early_stop
        self.n_hidden = n_hidden
        self.n_deep = n_deep
        self.l1_norm = l1_norm
        self.drop = drop
        self.patience = patience
        self.verbose = verbose
        self.learning_rate = learning_rate

    def fit(self, X, y, **kwargs):

        # Encoding labels
        self.le = LabelEncoder()
        self.y_ = self.le.fit_transform(y)
        self.n_class = len(self.le.classes_)

        if self.n_class == 2:
            out_dim = 1
        else:
            out_dim = self.n_class

        if hasattr(self, 'model'):
            self.reset_model()
        else:
            self.build_model(X.shape[1], out_dim)
        if self.verbose:
            temp = [layer['output_dim'] for layer in
                    self.model.get_config()['layers']
                    if layer['name'] == 'Dense']
            print('Model:{}'.format(temp))
            print('l1: {}, drop: {}, lr: {}, patience: {}'.format(
                self.l1_norm, self.drop, self.learning_rate,
                self.patience))

        return self

    def save(self, path):
        self.model.save_weights(path)

    def load(self, path):
        self.model.load_weights(path)

    def build_model(self, in_dim, out_dim):

        self.model = build_model(in_dim, out_dim=out_dim,
                                 n_hidden=self.n_hidden, l1_norm=self.l1_norm,
                                 n_deep=self.n_deep, drop=self.drop,
                                 learning_rate=self.learning_rate)
        self.w0 = self.model.get_weights()
        return self

    def reset_model(self):
        self.model.set_weights(self.w0)

    def feed_forward(self, X):
        # Feeds the model with X and returns the output of
        # each layer
        layer_output = []
        for layer in self.model.layers:
            if layer.get_config()['name'] == 'Dense':
                get_layer = function([self.model.layers[0].input],
                                     layer.get_output(train=False),
                                     allow_input_downcast=True)
                layer_output.append(get_layer(X))
        return layer_output

    def predict_proba(self, X):
        proba = self.model.predict(X, verbose=self.verbose)

        if proba.shape[1] == 1:
            proba = np.array(proba).reshape((X.shape[0], -1))
            temp = (1-proba.sum(axis=1)).reshape(X.shape[0], -1)
            proba = np.hstack((temp, proba))
        return proba

    def predict(self, X):
        prediction = self.model.predict_classes(X, verbose=self.verbose)
        prediction = np.array(prediction).reshape((X.shape[0], -1))
        prediction = np.squeeze(prediction).astype('int')
        return self.le.inverse_transform(prediction)

    def auc(self, X, y):
        prediction = self.predict(X)
        return roc_auc_score(y, prediction)

    def f1(self, X, y):
        prediction = self.predict(X)
        if self.n_class > 2:
            return f1_score(y, prediction, average='weighted')
        else:
            return f1_score(y, prediction)


class MLP(BaseMLP):

    def fit(self, X, y):
        super().fit(X, y)
        if self.n_class > 2:
            y = unroll(self.y_)
        else:
            y = self.y_

        if self.early_stop:
            sss = StratifiedShuffleSplit(self.y_, 1, test_size=0.1,
                                         random_state=0)
            train_index, val_index = next(iter(sss))
            x_train, x_val = X[train_index], X[val_index]
            y_train, y_val = y[train_index], y[val_index]

            stop = EarlyStopping(monitor='val_loss',
                                 patience=self.patience,
                                 verbose=self.verbose)
            self.model.fit(x_train, y_train,
                           nb_epoch=self.max_epoch,
                           # batch_size=64,
                           verbose=self.verbose,
                           callbacks=[stop],
                           show_accuracy=True,
                           validation_data=(x_val, y_val))
        else:
            self.model.fit(X, y, nb_epoch=self.max_epoch,
                           verbose=self.verbose,
                           show_accuracy=True)

        return self


class MLPg(MLP):

    def __init__(self, method='normal', decomposition_method='ica',
                 n_components=10, fine_tune=0,  n_hidden=1000, n_deep=4,
                 l1_norm=0.01, drop=0.1, early_stop=True, max_epoch=5000,
                 patience=200, learning_rate=1, verbose=2):
        super().__init__(n_hidden, n_deep, l1_norm, drop, early_stop,
                         max_epoch, patience, learning_rate, verbose)
        self.method = method
        self.decomposition_method = decomposition_method
        self.n_components = n_components
        self.fine_tune = fine_tune

    def fit(self, X, y, scaler=None):
        # Fit the model from a source of batches
        # batches: python iterator that generates the batches
        # batches_label: labels for all batches
        super(MLP, self).fit(X, y)

        if self.n_class > 2:
            y = unroll(self.y_)
        else:
            y = self.y_

        # Start generator
        logger.info('Starting generator ...')
        unscaled = scaler.inverse_transform(X)
        batches = dbg(unscaled,
                      self.y_, n_components=self.n_components,
                      n_batches=1000, method=self.method,
                      n_samples=500,
                      decomposition_method=self.decomposition_method)
        logger.info('Generator ready')

        best_loss = np.infty
        current_patience = 0
        for n, (batch, label) in enumerate(batches):
            if scaler:
                batch = scaler.transform(batch)
                
            old_label = label
            if self.n_class > 2:
                label = unroll(label)

            self.model.fit(batch, label, nb_epoch=10, verbose=0)

            loss_val = self.model.evaluate(X, y, verbose=0)

            if loss_val < best_loss:
                current_patience = 0
                best_loss = loss_val
                if self.verbose:
                    print('Best val_loss : %.3f' % best_loss)
            else:
                current_patience += 1
            if current_patience > self.patience:
                break

            if self.verbose:
                print('Batch {0:04d}: Train {1:02.2f}%, Val {2:02.2f}%'
                      .format(n, self.f1(batch, old_label)*100,
                              self.f1(X, y)*100))

        if self.fine_tune:
            self.model.fit(X, y, nb_epoch=self.fine_tune,
                           verbose=self.verbose)

        return self


def build_model(in_dim, out_dim=1,
                n_hidden=100, l1_norm=0.0,
                n_deep=5, drop=0.1,
                learning_rate=0.1):
    model = Sequential()
    # Input layer
    model.add(Dense(
        input_dim=in_dim,
        output_dim=n_hidden,
        init='glorot_uniform',
        activation='tanh',
        W_regularizer=l1(l1_norm)))

    # do X layers
    for layer in range(n_deep-1):
        model.add(Dropout(drop))
        model.add(Dense(
            output_dim=np.round(n_hidden/2**(layer+1)),
            init='glorot_uniform',
            activation='tanh',
            W_regularizer=l1(l1_norm)))

    # Output layer
    if out_dim == 1:
        activation = 'tanh'
    else:
        activation = 'softmax'

    model.add(Dense(out_dim,
                    init='glorot_uniform',
                    activation=activation))

    # Optimization algorithms
    opt = Adadelta(lr=learning_rate)
    if out_dim == 1:
        model.compile(loss='binary_crossentropy',
                      optimizer=opt,
                      class_mode='binary')
    else:
        model.compile(loss='categorical_crossentropy',
                      optimizer=opt,
                      class_mode='categorical')

    return model


def unroll(y):
    n_class = len(np.unique(y))
    return np.array([np.roll([1] + [0]*(n_class-1), pos) for pos in y])
