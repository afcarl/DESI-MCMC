"""
Celeste Source Derived Classes
"""
from CelestePy.models import Source, CelesteBase
from CelestePy.celeste_src import SrcParams
import autograd.numpy as np
from autograd import grad
import cPickle as pickle
from scipy.stats import multivariate_normal as mvn
import CelestePy.util.data as du

#############################
#source parameter priors    #
#############################
import os
prior_param_dir = os.path.join(os.path.dirname(__file__),
                               '../experiments/empirical_priors')
prior_param_dir = '../empirical_priors/'
from os.path import join
star_flux_mog = pickle.load(open(join(prior_param_dir, 'star_fluxes_mog.pkl'), 'rb'))
gal_flux_mog  = pickle.load(open(join(prior_param_dir, 'gal_fluxes_mog.pkl'), 'rb'))
gal_re_mog    = pickle.load(open(join(prior_param_dir, 'gal_re_mog.pkl'), 'rb'))
gal_ab_mog    = pickle.load(open(join(prior_param_dir, 'gal_ab_mog.pkl'), 'rb'))
star_mag_proposal = pickle.load(open(join(prior_param_dir, 'star_mag_proposal.pkl'), 'rb'))
gal_mag_proposal  = pickle.load(open(join(prior_param_dir, 'gal_mag_proposal.pkl'), 'rb'))
star_rad_proposal = pickle.load(open(join(prior_param_dir, 'star_res_proposal.pkl'), 'rb'))


def contains(pt, lower, upper):
    return np.all( (pt > lower) & (pt < upper) )


class SourceGMMPrior(Source):
    def __init__(self, params, model):
        super(SourceGMMPrior, self).__init__(params, model)

    def location_logprior(self, u):
        if contains(u, self.u_lower, self.u_upper):
            return 0.
        else:
            return -np.inf

    def resample(self):
        assert len(self.sample_image_list) != 0, "resample source needs sampled source images"
        if self.is_star():
            self.resample_star()
        elif self.is_galaxy():
            self.resample_galaxy()

    def constrain_loc(self, u_unc):
        u_unit = 1./(1. + np.exp(-u_unc))
        return u_unit * (self.u_upper - self.u_lower) + self.u_lower

    def unconstrain_loc(self, u):
        assert contains(u, self.u_lower, self.u_upper), "point not contained in initial interval!"
        # convert to unit interval, and then apply logit transformation
        u_unit = (u - self.u_lower) / (self.u_upper - self.u_lower)
        return np.log(u_unit) - np.log(1. - u_unit)

    def constrain_shape(self, lg_shape):
        lg_theta, lg_sigma, lg_phi, lg_rho = lg_shape
        theta = 1./(1. + np.exp(-lg_theta))
        sigma = np.exp(lg_sigma)
        phi   = 1./(1. + np.exp(-lg_phi)) * (180) + -180
        rho   = 1./(1. + np.exp(-lg_rho))
        return np.array([theta, sigma, phi, rho])

    def unconstrain_shape(self, shape):
        theta, sigma, phi, rho = shape
        lg_theta = np.log(theta) - np.log(1. - theta)
        lg_sigma = np.log(sigma)
        phi_unit = (phi+180) / 180.
        lg_phi   = np.log(phi_unit) - np.log(1. - phi_unit)
        lg_rho   = np.log(rho) - np.log(1. - rho)
        return np.array([lg_theta, lg_sigma, lg_phi, lg_rho])

    def resample_star(self):
        # jointly resample fluxes and location
        def loglike(th):
            u, color = self.constrain_loc(th[:2]), th[2:]  #unpack params
            fluxes   = np.exp(star_flux_mog.to_fluxes(color))
            ll       = self.log_likelihood(u=u, fluxes=fluxes)
            ll_color = star_flux_mog.logpdf(color)
            return ll+ll_color
        gloglike = grad(loglike)

        # pack params (make sure we convert to color first
        lfluxes = np.log(self.params.fluxes)
        th  = np.concatenate([self.unconstrain_loc(self.params.u),
                              star_flux_mog.to_colors(lfluxes)])
        print "initial conditional likelihood: %2.4f"%loglike(th)
        from scipy.optimize import minimize
        res = minimize(fun = lambda th: -1.*loglike(th),
                       jac = lambda th: -1.*gloglike(th),
                       x0=th,
                       method='L-BFGS-B',
                       options={'ftol' : 1e3 * np.finfo(float).eps})

        print res
        print "final conditional likelihood: %2.4f"%loglike(res.x)
        print gloglike(res.x)
        self.params.u      = self.constrain_loc(res.x[:2])
        self.params.fluxes = np.exp(star_flux_mog.to_fluxes(res.x[2:]))

    def resample_galaxy(self):
        # gradient w.r.t fluxes
        def loglike(th):
            # unpack location, color and shape parameters
            u, color, shape = self.constrain_loc(th[:2]), th[2:7], \
                              self.constrain_shape(th[7:])
            fluxes          = np.exp(gal_flux_mog.to_fluxes(color))
            ll              = self.log_likelihood(u=u, fluxes=fluxes, shape=shape)
            ll_color        = gal_flux_mog.logpdf(color)
            return ll+ll_color
        gloglike = grad(loglike)

        #print "initial conditional likelihood: %2.4f"%loglike(th)
        self.params.theta = np.clip(self.params.theta, 1e-6, 1-1e-6)
        th  = np.concatenate([self.unconstrain_loc(self.params.u),
                              gal_flux_mog.to_colors(np.log(self.params.fluxes)),
                              self.unconstrain_shape(self.params.shape)])
        print "initiali th: ", th
        from scipy.optimize import minimize
        res = minimize(fun = lambda th: -1.*loglike(th),
                       jac = lambda th: -1.*gloglike(th),
                       x0  = th,
                       method='L-BFGS-B', options={'disp':1, 'maxiter':10})

        # store new values
        self.params.u      = self.constrain_loc(res.x[:2])
        self.params.fluxes = np.exp(gal_flux_mog.to_fluxes(res.x[2:7]))
        self.params.shape  = self.constrain_shape(res.x[7:])

    def linear_propose_other_type(self):
        """ based on linear regression of fluxes and conditional distribution
        of galaxy shapes, propose parameters of the other type and report
        the log probability of generating that proposal

        Returns:
            - proposal params
            - log prob of proposal
            - log prob of implied reverse proposal
            - log determinant of the transformation |d(x',u')/d(x,u)|

        """
        params = SrcParams(u=self.params.u)
        if self.is_star():
            params.a = 1

            # fluxes
            residual = mvn.rvs(cov=star_mag_proposal.res_covariance)
            ll_prop_fluxes = mvn.logpdf(residual, mean=None, cov=star_mag_proposal.res_covariance)
            gal_mag = star_mag_proposal.predict(self.params.mags.reshape((1,-1))) + residual
            params.fluxes = du.mags2nanomaggies(gal_mag).flatten()

            # compute reverse ll
            res   = gal_mag_proposal.predict(gal_mag) - self.params.mags
            llrev = mvn.logpdf(res, mean=None, cov=gal_mag_proposal.res_covariance)

            # shape
            sample_re = star_rad_proposal.rvs(size=1)[0]
            ll_shape  = star_rad_proposal.logpdf(sample_re)
            params.shape = np.array([np.random.rand(),
                                     np.exp(sample_re),
                                     np.random.rand() * np.pi,
                                     np.random.rand()])
            _, logdet = np.linalg.slogdet(star_mag_proposal.coef_)
            return params, ll_prop_fluxes + ll_shape, llrev, logdet

        elif self.is_galaxy():
            params.a = 0
            # fluxes
            residual = mvn.rvs(cov=gal_mag_proposal.res_covariance)
            llprob   = mvn.logpdf(residual, mean=None, cov=gal_mag_proposal.res_covariance)
            star_mag = gal_mag_proposal.predict(self.params.mags.reshape((1, -1))) + residual
            params.fluxes = du.mags2nanomaggies(star_mag).flatten()

            res   = star_mag_proposal.predict(star_mag) - self.params.mags
            llrev = mvn.logpdf(res, mean=None, cov=star_mag_proposal.res_covariance)
            ll_re = star_rad_proposal.logpdf(np.log(self.params.sigma))

            _, logdet = np.linalg.slogdet(gal_mag_proposal.coef_)
            return params, llprob, llrev + ll_re, logdet


# Create universe model with this source type
class CelesteGMMPrior(CelesteBase):
    _source_type = SourceGMMPrior

    def __init__(self, star_flux_prior   = star_flux_mog,
                       galaxy_flux_prior = gal_flux_mog,
                       galaxy_re_prior   = gal_re_mog,
                       galaxy_ab_prior   = gal_ab_mog):
        self.star_flux_prior    = star_flux_prior
        self.galaxy_flux_prior  = galaxy_flux_prior
        self.galaxy_re_prior    = galaxy_re_prior
        self.galaxy_ab_prior    = galaxy_ab_prior
        super(CelesteGMMPrior, self).__init__()

    def logprior(self, params):
        if params.is_star():
            color = self.star_flux_prior.to_colors(params.fluxes)
            return self.star_flux_prior.logpdf(color)
        elif params.is_galaxy():
            color = self.galaxy_flux_prior.to_colors(params.fluxes)
            return self.galaxy_flux_prior.logpdf(color) + 0.
                    # todo include constraints for shape parameters

    def prior_sample(self, src_type, u=None):
        params = SrcParams(u=u)
        if src_type == 'star':
            # TODO SET a with atoken
            params.a = 0
            color   = self.star_flux_prior.rvs(size=1)[0]
            logprob = self.star_flux_prior.logpdf(color)
            params.fluxes = np.exp(self.star_flux_prior.to_fluxes(color))
            return params, logprob

        elif src_type == 'galaxy':
            params.a = 1
            color   = self.galaxy_flux_prior.rvs(size=1)[0]
            logprob = self.galaxy_flux_prior.logpdf(color)
            params.fluxes = np.exp(self.galaxy_flux_prior.to_fluxes(color))

            sample_ab = self.galaxy_ab_prior.rvs(size=1)[0,0]
            sample_ab = np.exp(sample_ab) / (1.+np.exp(sample_ab))

            params.shape  = np.array([np.random.random(),
                                      np.exp(self.galaxy_re_prior.rvs(size=1)[0,0]),
                                      np.random.random() * np.pi,
                                      sample_ab])

            logprob_re    = self.galaxy_re_prior.logpdf(params.sigma)
            logprob_ab    = self.galaxy_ab_prior.logpdf(params.rho)
            logprob_shape = -np.log(np.pi) + logprob_re + logprob_ab
            return params, logprob


