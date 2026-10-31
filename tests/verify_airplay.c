/* Verify both classic AirPlay codecs and decode a synthetic big-endian PCM packet. */
#include <libavcodec/avcodec.h>
#include <libavutil/channel_layout.h>
#include <libavutil/samplefmt.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    const AVCodec *alac = avcodec_find_decoder(AV_CODEC_ID_ALAC);
    const AVCodec *pcm = avcodec_find_decoder(AV_CODEC_ID_PCM_S16BE);
    if (!alac || !pcm) {
        fputs("Missing a required classic AirPlay decoder.\n", stderr);
        return 1;
    }
    AVCodecContext *context = avcodec_alloc_context3(pcm);
    AVPacket *packet = av_packet_alloc();
    AVFrame *frame = av_frame_alloc();
    if (!context || !packet || !frame) return 1;
    context->sample_rate = 44100;
    av_channel_layout_default(&context->ch_layout, 2);
    if (avcodec_open2(context, pcm, NULL) < 0 || av_new_packet(packet, 352 * 4) < 0) return 1;
    for (int i = 0; i < 352; i++) {
        const uint8_t stereo[] = {0x03, 0xe8, 0xfc, 0x18};
        memcpy(packet->data + i * 4, stereo, 4);
    }
    if (avcodec_send_packet(context, packet) < 0 || avcodec_receive_frame(context, frame) < 0)
        return 1;
    int result = 0;
    if (frame->nb_samples != 352 || frame->format != AV_SAMPLE_FMT_S16 ||
        frame->ch_layout.nb_channels != 2) result = 1;
    if (!result) {
        const int16_t *data = (const int16_t *)frame->data[0];
        for (int i = 0; i < 352; i++)
            if (data[i * 2] != 1000 || data[i * 2 + 1] != -1000) result = 1;
    }
    av_frame_free(&frame);
    av_packet_free(&packet);
    avcodec_free_context(&context);
    if (result) fputs("Classic AirPlay PCM decode did not preserve stereo samples.\n", stderr);
    else puts("PASS: ALAC and PCM codecs present; 352 synthetic PCM frames decoded correctly.");
    return result;
}
