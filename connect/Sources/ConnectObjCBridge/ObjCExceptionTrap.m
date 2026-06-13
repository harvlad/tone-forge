//
// ObjCExceptionTrap.m
//
// See ConnectObjCBridge.h for rationale.
//

#import "ConnectObjCBridge.h"

@implementation ObjCExceptionTrap

+ (BOOL)tryBlock:(__attribute__((noescape)) void (^)(void))block
           error:(NSError * _Nullable * _Nullable)errorOut {
    @try {
        block();
        return YES;
    } @catch (NSException *ex) {
        if (errorOut) {
            NSMutableDictionary *info = [NSMutableDictionary dictionary];
            // The reason string is the human-readable one the runtime
            // would have printed before SIGABRT. Surface it as the
            // localized description so default NSError formatting on
            // the Swift side is meaningful.
            info[NSLocalizedDescriptionKey] = ex.reason ?: @"(no reason)";
            info[@"ExceptionName"] = ex.name ?: @"(no name)";
            if (ex.callStackSymbols) {
                info[@"ExceptionCallStack"] = ex.callStackSymbols;
            }
            *errorOut = [NSError errorWithDomain:@"ToneForgeConnect.ObjCException"
                                            code:0
                                        userInfo:info];
        }
        return NO;
    }
}

@end
