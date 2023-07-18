#include <cupy/complex.cuh>
extern "C" __global__
void doppler(float *color,
             float *power,
             const complex<float> *iqFrames,
             const int nFrames,
             const int nx,
             const int nz)
{
    int z = blockIdx.x * blockDim.x + threadIdx.x;
    int x = blockIdx.y * blockDim.y + threadIdx.y;
    if (z >= nz || x >= nx) {
        return;
    }
    /* Color estimation */
    complex<float> iqCurrent, iqPrevious;
    float ic, qc, ip, qp, pwr, nom = 0.0f, den = 0.0f;

    iqCurrent = iqFrames[z + x*nz];
    ic = real(iqCurrent);
    qc = imag(iqCurrent);
    pwr = ic*ic + qc*qc;
    for (int iFrame = 1; iFrame < nFrames; ++iFrame) {
        // previous I and Q values
        ip = ic;
        qp = qc;
        // current I and Q values
        iqCurrent = iqFrames[z + x*nz + iFrame*nz*nx];
        ic = real(iqCurrent);
        qc = imag(iqCurrent);
        pwr += ic*ic + qc*qc;
        den += ic*ip + qc*qp;
        nom += qc*ip - ic*qp;
    }
    color[z + x*nz] = atan2f(nom, den);
    power[z + x*nz] = pwr/nFrames;
}
