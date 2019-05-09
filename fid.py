# borrowed from https://github.com/ajbrock/BigGAN-PyTorch/blob/master/inception_utils.py
import torch
import numpy as np
from tqdm import tqdm
import torch.nn as nn
from scipy import linalg
import torch.nn.functional as F
from torchvision import transforms
from torch.nn import Parameter as P
from torchvision.datasets import MNIST
from torch.utils.data import DataLoader
from torchvision.models.inception import inception_v3

from utils import to


# Module that wraps the inception network to return pooled features
class WrapInception(nn.Module):
    def __init__(self, net):
        super().__init__()
        self.net = net
        self.mean = P(torch.tensor([0.485, 0.456, 0.406]).view(1, -1, 1, 1), requires_grad=False)
        self.std = P(torch.tensor([0.229, 0.224, 0.225]).view(1, -1, 1, 1), requires_grad=False)

    def forward(self, x):
        # Normalize x
        x = (x + 1.) / 2.0
        x = (x - self.mean) / self.std
        # Upsample if necessary
        if x.shape[2] != 299 or x.shape[3] != 299:
            x = F.interpolate(x, size=(299, 299), mode='bilinear', align_corners=True)
        # 299 x 299 x 3
        x = self.net.Conv2d_1a_3x3(x)
        # 149 x 149 x 32
        x = self.net.Conv2d_2a_3x3(x)
        # 147 x 147 x 32
        x = self.net.Conv2d_2b_3x3(x)
        # 147 x 147 x 64
        x = F.max_pool2d(x, kernel_size=3, stride=2)
        # 73 x 73 x 64
        x = self.net.Conv2d_3b_1x1(x)
        # 73 x 73 x 80
        x = self.net.Conv2d_4a_3x3(x)
        # 71 x 71 x 192
        x = F.max_pool2d(x, kernel_size=3, stride=2)
        # 35 x 35 x 192
        x = self.net.Mixed_5b(x)
        # 35 x 35 x 256
        x = self.net.Mixed_5c(x)
        # 35 x 35 x 288
        x = self.net.Mixed_5d(x)
        # 35 x 35 x 288
        x = self.net.Mixed_6a(x)
        # 17 x 17 x 768
        x = self.net.Mixed_6b(x)
        # 17 x 17 x 768
        x = self.net.Mixed_6c(x)
        # 17 x 17 x 768
        x = self.net.Mixed_6d(x)
        # 17 x 17 x 768
        x = self.net.Mixed_6e(x)
        # 17 x 17 x 768
        # 17 x 17 x 768
        x = self.net.Mixed_7a(x)
        # 8 x 8 x 1280
        x = self.net.Mixed_7b(x)
        # 8 x 8 x 2048
        x = self.net.Mixed_7c(x)
        # 8 x 8 x 2048
        pool = torch.mean(x.view(x.size(0), x.size(1), -1), 2)
        # 2048
        return pool


# A pytorch implementation of cov, from Modar M. Alfadly
# https://discuss.pytorch.org/t/covariance-and-gradient-support/16217/2
def torch_cov(m, rowvar=False):
    """Estimate a covariance matrix given data.

    Covariance indicates the level to which two variables vary together.
    If we examine N-dimensional samples, `X = [x_1, x_2, ... x_N]^T`,
    then the covariance matrix element `C_{ij}` is the covariance of
    `x_i` and `x_j`. The element `C_{ii}` is the variance of `x_i`.

    Args:
        m: A 1-D or 2-D array containing multiple variables and observations.
            Each row of `m` represents a variable, and each column a single
            observation of all those variables.
        rowvar: If `rowvar` is True, then each row represents a
            variable, with observations in the columns. Otherwise, the
            relationship is transposed: each column represents a variable,
            while the rows contain observations.

    Returns:
        The covariance matrix of the variables.
    """
    if m.dim() > 2:
        raise ValueError('m has more than 2 dimensions')
    if m.dim() < 2:
        m = m.view(1, -1)
    if not rowvar and m.size(0) != 1:
        m = m.t()
    # m = m.type(torch.double)  # uncomment this line if desired
    fact = 1.0 / (m.size(1) - 1)
    m -= torch.mean(m, dim=1, keepdim=True)
    mt = m.t()  # if complex: mt = m.t().conj()
    return fact * m.matmul(mt).squeeze()


# Pytorch implementation of matrix sqrt, from Tsung-Yu Lin, and Subhransu Maji
# https://github.com/msubhransu/matrix-sqrt
def sqrt_newton_schulz(A, numIters, dtype=None):
    with torch.no_grad():
        if dtype is None:
            dtype = A.type()
        batchSize = A.shape[0]
        dim = A.shape[1]
        normA = A.mul(A).sum(dim=1).sum(dim=1).sqrt()
        Y = A.div(normA.view(batchSize, 1, 1).expand_as(A));
        I = torch.eye(dim, dim).view(1, dim, dim).repeat(batchSize, 1, 1).type(dtype)
        Z = torch.eye(dim, dim).view(1, dim, dim).repeat(batchSize, 1, 1).type(dtype)
        for i in range(numIters):
            T = 0.5 * (3.0 * I - Z.bmm(Y))
            Y = Y.bmm(T)
            Z = T.bmm(Z)
        sA = Y * torch.sqrt(normA).view(batchSize, 1, 1).expand_as(A)
    return sA


# FID calculator from TTUR
def numpy_calculate_frechet_distance(mu1, sigma1, mu2, sigma2, eps=1e-6):
    """Numpy implementation of the Frechet Distance.
    Taken from https://github.com/bioinf-jku/TTUR
    The Frechet distance between two multivariate Gaussians X_1 ~ N(mu_1, C_1)
    and X_2 ~ N(mu_2, C_2) is
            d^2 = ||mu_1 - mu_2||^2 + Tr(C_1 + C_2 - 2*sqrt(C_1*C_2)).
    Stable version by Dougal J. Sutherland.
    Params:
    -- mu1   : Numpy array containing the activations of a layer of the
               inception net (like returned by the function 'get_predictions')
               for generated samples.
    -- mu2   : The sample mean over activations, precalculated on an
               representive data set.
    -- sigma1: The covariance matrix over activations for generated samples.
    -- sigma2: The covariance matrix over activations, precalculated on an
               representive data set.
    Returns:
    --   : The Frechet Distance.
    """

    mu1 = np.atleast_1d(mu1)
    mu2 = np.atleast_1d(mu2)

    sigma1 = np.atleast_2d(sigma1)
    sigma2 = np.atleast_2d(sigma2)

    assert mu1.shape == mu2.shape, \
        'Training and test mean vectors have different lengths'
    assert sigma1.shape == sigma2.shape, \
        'Training and test covariances have different dimensions'

    diff = mu1 - mu2

    # Product might be almost singular
    covmean, _ = linalg.sqrtm(sigma1.dot(sigma2), disp=False)
    if not np.isfinite(covmean).all():
        msg = ('fid calculation produces singular product; '
               'adding %s to diagonal of cov estimates') % eps
        print(msg)
        offset = np.eye(sigma1.shape[0]) * eps
        covmean = linalg.sqrtm((sigma1 + offset).dot(sigma2 + offset))

    # Numerical error might give slight imaginary component
    if np.iscomplexobj(covmean):
        print('wat')
        if not np.allclose(np.diagonal(covmean).imag, 0, atol=1e-3):
            m = np.max(np.abs(covmean.imag))
            raise ValueError('Imaginary component {}'.format(m))
        covmean = covmean.real

    tr_covmean = np.trace(covmean)

    out = diff.dot(diff) + np.trace(sigma1) + np.trace(sigma2) - 2 * tr_covmean
    return out


def torch_calculate_frechet_distance(mu1, sigma1, mu2, sigma2, eps=1e-6):
    """Pytorch implementation of the Frechet Distance.
    Taken from https://github.com/bioinf-jku/TTUR
    The Frechet distance between two multivariate Gaussians X_1 ~ N(mu_1, C_1)
    and X_2 ~ N(mu_2, C_2) is
            d^2 = ||mu_1 - mu_2||^2 + Tr(C_1 + C_2 - 2*sqrt(C_1*C_2)).
    Stable version by Dougal J. Sutherland.
    Params:
    -- mu1   : Numpy array containing the activations of a layer of the
               inception net (like returned by the function 'get_predictions')
               for generated samples.
    -- mu2   : The sample mean over activations, precalculated on an
               representive data set.
    -- sigma1: The covariance matrix over activations for generated samples.
    -- sigma2: The covariance matrix over activations, precalculated on an
               representive data set.
    Returns:
    --   : The Frechet Distance.
    """

    assert mu1.shape == mu2.shape, \
        'Training and test mean vectors have different lengths'
    assert sigma1.shape == sigma2.shape, \
        'Training and test covariances have different dimensions'

    diff = mu1 - mu2
    # Run 50 itrs of newton-schulz to get the matrix sqrt of sigma1 dot sigma2
    covmean = sqrt_newton_schulz(sigma1.mm(sigma2).unsqueeze(0), 50).squeeze()
    out = (diff.dot(diff) + torch.trace(sigma1) + torch.trace(sigma2)
           - 2 * torch.trace(covmean))
    return out


# Loop and run the sampler and the net until it accumulates num_inception_images
# activations. Return the pooled features
def accumulate_inception_activations(sample, net, num_inception_images=20000):
    pool = []
    current_count = 0
    while current_count < num_inception_images:
        with torch.no_grad():
            pool_val = net(sample())
            pool += [pool_val]
            current_count += pool_val.shape[0]
    return torch.cat(pool, 0)


# Load and wrap the Inception model
def load_inception_net():
    return to(WrapInception(inception_v3(pretrained=True, transform_input=False).eval()))


# This produces a function which takes in an iterator which returns a set
# number of samples and iterates until it accumulates 20000 images.
def prepare_inception_metrics():
    # Load metrics; this is intentionally not in a try-except loop so that
    # the script will crash here if it cannot find the Inception moments.
    inception_moments = np.load('inception_moments.npz')
    data_mu = inception_moments['mu']
    data_sigma = inception_moments['sigma']
    # Load network
    net = load_inception_net()

    def get_inception_metrics(sample_fn, num_inception_images=20000, use_torch=True):
        pool = accumulate_inception_activations(sample_fn, net, num_inception_images)
        if use_torch:
            mu, sigma = torch.mean(pool, 0), torch_cov(pool, rowvar=False)
            FID = torch_calculate_frechet_distance(mu, sigma, to(torch.tensor(data_mu).float()),
                                                   to(torch.tensor(data_sigma).float()))
            FID = float(FID.cpu().numpy())
        else:
            mu, sigma = np.mean(pool.cpu().numpy(), axis=0), np.cov(pool.cpu().numpy(), rowvar=False)
            FID = numpy_calculate_frechet_distance(mu.cpu().numpy(), sigma.cpu().numpy(), data_mu, data_sigma)
        # Delete mu, sigma, pool, just in case
        del mu, sigma, pool
        return FID

    return get_inception_metrics


def main(batch_size=256):
    train_loader = DataLoader(MNIST('~/.torch/data/', train=True, download=True,
                                    transform=transforms.Compose([transforms.ToTensor(),
                                                                  transforms.Normalize((0.1307,),
                                                                                       (0.3081,))])),
                              batch_size=batch_size, shuffle=True, drop_last=False)
    net = load_inception_net()
    pool = []
    with torch.no_grad():
        for i, (x, y) in enumerate(tqdm(train_loader)):
            pool += [net(to(x)).cpu().numpy()]
    pool = np.concatenate(pool, 0)
    # Prepare mu and sigma, save to disk.
    mu, sigma = np.mean(pool, axis=0), np.cov(pool, rowvar=False)
    np.savez('inception_moments.npz', **{'mu': mu, 'sigma': sigma})


if __name__ == '__main__':
    main()