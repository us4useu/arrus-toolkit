#include <cupy/complex.cuh>
#include <math_constants.h>

#define CUDART_PI_F 3.141592654f

extern "C"
__global__ void
iqRaw2Hri(complex<float> *iqLri, const complex<float> *iqRaw,
          const int nTx, const int nSamp, const int nRx,
          const float *zPixGrid, const int nZPix,
          const float *xPixGrid, const int nXPix,
          const float *yPixGrid, const int nYPix,
          float const sos, float const fs, float const fn,
          const float *txFoc, const float *txAng,
          const float *txApCent,
          const float txProbePitch, const int txProbeNElem, const unsigned char txProbeOri,
          const float rxProbePitch, const int rxProbeNElem, const unsigned char rxProbeOri,
          const float *txApFstElemPos, const float *txApLstElemPos,
          const int *rxApOrigElem,
          const float minRxTang, const float maxRxTang,
          float const initDel,
          const float nSigma) {

    int z = blockIdx.x * blockDim.x + threadIdx.x;
    int x = blockIdx.y * blockDim.y + threadIdx.y;
    int y = blockIdx.z * blockDim.z + threadIdx.z;

    if(z >= nZPix || x >= nXPix || y >= nYPix) {
        return;
    }
    float zPix = zPixGrid[z];
    float xPix = xPixGrid[x];
    float yPix = yPixGrid[y];

    float txLateralPix, rxLateralPix;
    float rxElem;

    txLateralPix = txProbeOri == 0 ? xPix : yPix;

    int iElem, offset;
    float interpWgh;
    float txDist, rxDist, rxTang, txApod, rxApod, time, iSamp;
    float modSin, modCos, pixWgh;
    const float omega = 2 * CUDART_PI_F * fn;
    const float sosInv = 1 / sos;
    const float twoSigSqrInv = nSigma * nSigma * 0.5f;
    const float rngRxTangInv = 2 / (maxRxTang - minRxTang); // inverted half range
    const float centRxTang = (maxRxTang + minRxTang) * 0.5f;
    complex<float> pix(0.0f, 0.0f), samp(0.0f, 0.0f), modFactor;

    iqLri[z + x*nZPix + y*nZPix*nXPix] = complex<float>(0.0f, 0.0f);

    for(int iTx = 0; iTx < nTx; ++iTx) {
        int txOffset = iTx*nSamp*nRx;
        if(!isinf(txFoc[iTx])) {
            /* STA */

            float zFoc = txFoc[iTx]*cosf(txAng[iTx]);
            float lateralFoc = txApCent[iTx] + txFoc[iTx] * sinf(txAng[iTx]);

            float pixFocArrang;
            if(txFoc[iTx] <= 0.0f) {
                /* Virtual Point Source BEHIND probe surface */
                // Valid pixels are assumed to be always in front of the focal point (VSP)
                pixFocArrang = 1.0f;
            } else {
                /* Virtual Point Source IN FRONT OF probe surface */
                // Projection of the Foc-Pix vector on the ApCent-Foc vector (dot product) ...
                // to determine if the pixel is behind (-) or in front of (+) the focal point (VSP).
                pixFocArrang = (((zPix - zFoc) * zFoc +
                                 (txLateralPix - lateralFoc) * (lateralFoc-txApCent[iTx])) >= 0.f) ? 1.f : -1.f;
            }
            txDist = hypotf(zPix-zFoc, txLateralPix-lateralFoc);
            txDist *= pixFocArrang; // Compensation for the Pix-Foc arrangement
            txDist += txFoc[iTx]; // Compensation for the reference time being the moment when txApCent fires.

            // Projections of Foc-Pix vector on the rotated Foc-ApEdge vectors (dot products) ...
            // to determine if the pixel is in the sonified area (dot product >= 0).
            // Foc-ApEdgeFst vector is rotated left, Foc-ApEdgeLst vector is rotated right.
            txApod = (((-(txApFstElemPos[iTx] - lateralFoc) * (zPix-zFoc) +
                        (-zFoc)*(txLateralPix - lateralFoc)) * pixFocArrang >= 0.f) &&
                      (((txApLstElemPos[iTx] - lateralFoc) * (zPix-zFoc) -
                        (-zFoc)*(txLateralPix - lateralFoc)) * pixFocArrang >= 0.f)) ? 1.f : 0.f;
        } else {
            /* PWI */
            txDist = zPix * cosf(txAng[iTx]) + (txLateralPix - txApCent[iTx]) * sinf(txAng[iTx]);
            // Projections of ApEdge-Pix vector on the rotated unit vector of tx direction (dot products) ...
            // to determine if the pixel is in the sonified area (dot product >= 0).
            // For ApEdgeFst, the vector is rotated left, for ApEdgeLst the vector is rotated right.
            txApod = (((-zPix * sinf(txAng[iTx]) +
                        (txLateralPix - txApFstElemPos[iTx]) * cosf(txAng[iTx])) >= 0.f) &&
                      ((zPix * sinf(txAng[iTx]) -
                        (txLateralPix - txApLstElemPos[iTx]) * cosf(txAng[iTx])) >= 0.f)) ? 1.f : 0.f;
        }
        pixWgh = 0.0f;
        pix.real(0.0f);
        pix.imag(0.0f);

        if(txApod != 0.0f) {
            rxElem = -((float)(rxProbeNElem-1) / 2.0f)*rxProbePitch;
            for(int iRx = 0; iRx < nRx; ++iRx) {
                iElem = iRx + rxApOrigElem[iTx];
                rxElem += rxProbePitch;
                if(iElem < 0 || iElem >= rxProbeNElem) continue;
                rxLateralPix = rxProbeOri == 0 ? xPix : yPix;
                rxDist = hypotf(rxLateralPix - rxElem, zPix);
                rxTang = __fdividef(rxLateralPix - rxElem, zPix);
                if(rxTang < minRxTang || rxTang > maxRxTang) continue;
                rxApod = (rxTang-centRxTang) * rngRxTangInv;
                rxApod = __expf(-rxApod*rxApod*twoSigSqrInv);
                time = (txDist+rxDist)*sosInv + initDel;
                iSamp = time*fs;
                if(iSamp < 0.0f || iSamp >= static_cast<float>(nSamp - 1)) {
                    continue;
                }
                offset = txOffset + iRx*nSamp;
                interpWgh = modff(iSamp, &iSamp);
                int intSamp = int(iSamp);
                __sincosf(omega*time, &modSin, &modCos);
                complex<float> modFactor = complex<float>(modCos, modSin);
                samp = iqRaw[offset+intSamp]*(1-interpWgh) + iqRaw[offset+intSamp+1]*interpWgh;
                pix += samp*modFactor*rxApod;
                pixWgh += rxApod;
            }
        }
        if(pixWgh != 0.0f) {
            iqLri[z + x*nZPix + y*nZPix*nXPix] += pix/pixWgh*txApod;
        }
    }
}
