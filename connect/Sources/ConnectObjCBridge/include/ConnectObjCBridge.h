//
// ConnectObjCBridge.h
//
// ObjC support for catching Objective-C NSException across the
// Swift boundary. Swift's `do/try/catch` cannot catch NSException
// — only thrown Swift Errors. AVAudioEngine's `connect(_:to:format:)`
// raises NSInvalidArgumentException synchronously when the input
// node's hardware format does not match the destination tap format
// (the textbook trigger is hot-plugging an audio interface whose
// reported channel count flips between reconfigure attempts —
// e.g. the Line 6 HX Stomp going through stereo→mono→stereo as the
// host re-enumerates it). Without a trap the process is killed with
// SIGABRT and the helper has to be supervised back up.
//
// The trap is intentionally narrow: callers wrap only the specific
// AVAudioEngine.connect calls that can throw. Any non-format
// exception is rethrown — we want to know about programmer error,
// not silently swallow it.
//

#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

@interface ObjCExceptionTrap : NSObject

/// Executes ``block`` inside an Objective-C ``@try/@catch`` and
/// converts any NSException into an NSError so Swift callers can
/// handle it the normal way.
///
/// Returns ``YES`` on clean execution, ``NO`` if an exception was
/// caught. When ``NO`` is returned and ``errorOut`` is non-NULL,
/// the populated NSError carries:
///
///   - domain:   "ToneForgeConnect.ObjCException"
///   - code:     0
///   - userInfo[NSLocalizedDescriptionKey]: exception.reason
///   - userInfo["ExceptionName"]:            exception.name
///   - userInfo["ExceptionCallStack"]:       exception.callStackSymbols
///
/// The block is marked ``noescape``; the trap is a synchronous
/// scope so the caller can rely on local stack semantics.
+ (BOOL)tryBlock:(__attribute__((noescape)) void (^)(void))block
           error:(NSError * _Nullable * _Nullable)errorOut;

@end

NS_ASSUME_NONNULL_END
