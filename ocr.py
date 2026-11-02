"""macOS Vision으로 화면 일부의 글자를 읽는다 (한국어)."""
import Quartz
import Vision
from Foundation import NSData


def read_text(png_bytes):
    """PNG 바이트 → 인식된 줄 목록."""
    data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
    src = Quartz.CGImageSourceCreateWithData(data, None)
    img = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(['ko-KR', 'en-US'])
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(img, None)
    ok, _ = handler.performRequests_error_([req], None)
    if not ok:
        return []
    return [r.topCandidates_(1)[0].string() for r in (req.results() or [])]
