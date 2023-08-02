import cupy as cp
from arrus.utils.imaging import *

_hilbert_kernel = cp.ElementwiseKernel(
    "",
    "T h",
    """
    if ( !odd ) {
        if ( ( i == 0 ) || ( i == bend ) ) {
            h = 1.0;
        } else if ( i > 0 && i < bend ) {
            h = 2.0;
        } else {
            h = 0.0;
        }
    } else {
        if ( i == 0 ) {
            h = 1.0;
        } else if ( i > 0 && i < bend) {
            h = 2.0;
        } else {
            h = 0.0;
        }
    }
    """,
    "_hilbert_kernel",
    options=("-std=c++11",),
    loop_prep="const bool odd { _ind.size() & 1 }; \
               const int bend = odd ? \
                   static_cast<int>( 0.5 * ( _ind.size()  + 1 ) ) : \
                   static_cast<int>( 0.5 * _ind.size() );",
)


def hilbert(x, N=None, axis=-1):
    """
    Compute the analytic signal, using the Hilbert transform.

    The transformation is done along the last axis by default.

    Parameters
    ----------
    x : array_like
        Signal data.  Must be real.
    N : int, optional
        Number of Fourier components.  Default: ``x.shape[axis]``
    axis : int, optional
        Axis along which to do the transformation.  Default: -1.

    Returns
    -------
    xa : ndarray
        Analytic signal of `x`, of each 1-D array along `axis`

    Notes
    -----
    The analytic signal ``x_a(t)`` of signal ``x(t)`` is:

    .. math:: x_a = F^{-1}(F(x) 2U) = x + i y

    where `F` is the Fourier transform, `U` the unit step function,
    and `y` the Hilbert transform of `x`. [1]_

    In other words, the negative half of the frequency spectrum is zeroed
    out, turning the real-valued signal into a complex signal.  The Hilbert
    transformed signal can be obtained from ``cp.imag(hilbert(x))``, and the
    original signal from ``cp.real(hilbert(x))``.

    Examples
    ---------
    In this example we use the Hilbert transform to determine the amplitude
    envelope and instantaneous frequency of an amplitude-modulated signal.

    >>> import cupy as cp
    >>> import matplotlib.pyplot as plt
    >>> from cusignal import hilbert, chirp

    >>> duration = 1.0
    >>> fs = 400.0
    >>> samples = int(fs*duration)
    >>> t = cp.arange(samples) / fs

    We create a chirp of which the frequency increases from 20 Hz to 100 Hz and
    apply an amplitude modulation.

    >>> signal = chirp(t, 20.0, t[-1], 100.0)
    >>> signal *= (1.0 + 0.5 * cp.sin(2.0*cp.pi*3.0*t) )

    The amplitude envelope is given by magnitude of the analytic signal. The
    instantaneous frequency can be obtained by differentiating the
    instantaneous phase in respect to time. The instantaneous phase corresponds
    to the phase angle of the analytic signal.

    >>> analytic_signal = hilbert(signal)
    >>> amplitude_envelope = cp.abs(analytic_signal)
    >>> instantaneous_phase = cp.unwrap(cp.angle(analytic_signal))
    >>> instantaneous_frequency = (cp.diff(instantaneous_phase) /
    ...                            (2.0*cp.pi) * fs)

    >>> fig = plt.figure()
    >>> ax0 = fig.add_subplot(211)
    >>> ax0.plot(cp.asnumpy(t), cp.asnumpy(signal), label='signal')
    >>> ax0.plot(cp.asnumpy(t), cp.asnumpy(amplitude_envelope), \
        label='envelope')
    >>> ax0.set_xlabel("time in seconds")
    >>> ax0.legend()
    >>> ax1 = fig.add_subplot(212)
    >>> ax1.plot(t[1:], instantaneous_frequency)
    >>> ax1.set_xlabel("time in seconds")
    >>> ax1.set_ylim(0.0, 120.0)

    References
    ----------
    .. [1] Wikipedia, "Analytic signal".
           https://en.wikipedia.org/wiki/Analytic_signal
    .. [2] Leon Cohen, "Time-Frequency Analysis", 1995. Chapter 2.
    .. [3] Alan V. Oppenheim, Ronald W. Schafer. Discrete-Time Signal
           Processing, Third Edition, 2009. Chapter 12.
           ISBN 13: 978-1292-02572-8

    """
    x = cp.asarray(x)
    if cp.iscomplexobj(x):
        raise ValueError("x must be real.")
    if N is None:
        N = x.shape[axis]
    if N <= 0:
        raise ValueError("N must be positive.")

    Xf = cp.fft.fft(x, N, axis=axis)

    h = cp.empty((N,), dtype=x.dtype)
    _hilbert_kernel(h)

    if x.ndim > 1:
        ind = [cp.newaxis] * x.ndim
        ind[axis] = slice(None)
        h = h[tuple(ind)]
    x = cp.fft.ifft(Xf * h, axis=axis)
    return x


class Hilbert(Operation):

    def __init__(self, axis=-1):
        self.axis = axis

    def prepare(self, const_metadata):
        return const_metadata.copy(dtype=cp.complex64)

    def process(self, data):
        return hilbert(data, axis=self.axis)
