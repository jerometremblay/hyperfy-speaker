#include <CoreAudio/AudioServerPlugIn.h>
#include <CoreFoundation/CoreFoundation.h>
#include <assert.h>
#include <dlfcn.h>
#include <stdio.h>

int main(int argc, char **argv) {
    assert(argc == 2);
    void *library = dlopen(argv[1], RTLD_NOW);
    if (!library) {
        fprintf(stderr, "Could not load driver: %s\n", dlerror());
        return 1;
    }
    void *(*create)(CFAllocatorRef, CFUUIDRef) = dlsym(library, "BlackHole_Create");
    assert(create);
    AudioServerPlugInDriverRef driver = create(NULL, kAudioServerPlugInTypeUUID);
    assert(driver);
    AudioObjectPropertyAddress property = {
        kAudioObjectPropertyName, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain
    };
    CFStringRef name = NULL;
    UInt32 size = sizeof(name);
    assert((*driver)->GetPropertyData(driver, 3, 0, &property, 0, NULL, size, &size, &name) == noErr);
    assert(CFEqual(name, CFSTR("Hyperfy Speaker")));
    CFRelease(name);
    property.mSelector = kAudioDevicePropertyNominalSampleRate;
    Float64 rate = 0;
    size = sizeof(rate);
    assert((*driver)->GetPropertyData(driver, 3, 0, &property, 0, NULL, size, &size, &rate) == noErr);
    assert(rate == 48000);
    property.mSelector = kAudioStreamPropertyVirtualFormat;
    AudioStreamBasicDescription format = {0};
    size = sizeof(format);
    // Verify both the input and output stream exposed by the compiled HAL driver.
    for (AudioObjectID stream = 4; stream <= 7; stream += 3) {
        assert((*driver)->GetPropertyData(
            driver, stream, 0, &property, 0, NULL, size, &size, &format
        ) == noErr);
        assert(format.mChannelsPerFrame == 2 && format.mSampleRate == 48000);
    }
    puts("Driver contract passed: Hyperfy Speaker, stereo input/output, 48 kHz.");
    return 0;
}
