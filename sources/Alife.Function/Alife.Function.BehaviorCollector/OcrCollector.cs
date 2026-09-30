using System;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using Alife.Foundation;
using Windows.Graphics.Imaging;
using Windows.Media.Ocr;
using Windows.Storage;

namespace Alife.Function.BehaviorCollector;

/// <summary>定时截屏并用 Windows 自带 OCR 识别文字，只保存文字，不保存截图</summary>
public class OcrCollector(BehaviorStore store)
{
    const int MaxTextLength = 2000;
    const int MaxEntries = 100;

    static OcrEngine? engine;

    public async Task TickAsync(CancellationToken cancellationToken)
    {
        string text;
        using (Bitmap screen = Win32.CaptureScreen())
            text = await RecognizeAsync(screen, cancellationToken);

        if (string.IsNullOrWhiteSpace(text))
            return;

        TextEntry entry = new() {
            Time = DateTime.Now.ToString("HH:mm"),
            Text = text.Length > MaxTextLength ? text[..MaxTextLength] : text
        };
        store.AppendToday<TextEntry>(BehaviorStore.OcrLogFile, entries => entries.Add(entry), MaxEntries);
    }

    static async Task<string> RecognizeAsync(Bitmap image, CancellationToken cancellationToken)
    {
        OcrEngine ocr = engine ??= CreateEngine()
                                   ?? throw new InvalidOperationException("系统未安装可用的 OCR 语言包");

        string tempFile = Path.Combine(AlifePath.TempFolderPath, $"behavior_ocr_{Guid.NewGuid():N}.png");
        try
        {
            image.Save(tempFile, System.Drawing.Imaging.ImageFormat.Png);

            StorageFile file = await StorageFile.GetFileFromPathAsync(tempFile);
            using var stream = await file.OpenAsync(FileAccessMode.Read);
            BitmapDecoder decoder = await BitmapDecoder.CreateAsync(stream);
            using SoftwareBitmap bitmap = await decoder.GetSoftwareBitmapAsync(
                BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied);

            OcrResult result = await ocr.RecognizeAsync(bitmap).AsTask(cancellationToken);
            string text = string.Join("\n", result.Lines.Select(line => line.Text));
            // Windows OCR 会在汉字之间插入空格，这里去掉
            return Regex.Replace(text, @"(?<=[一-龥])\s+(?=[一-龥])", "");
        }
        finally
        {
            File.Delete(tempFile);
        }
    }

    static OcrEngine? CreateEngine()
    {
        OcrEngine? created = OcrEngine.TryCreateFromUserProfileLanguages();
        if (created != null)
            return created;
        Windows.Globalization.Language? language = OcrEngine.AvailableRecognizerLanguages.FirstOrDefault();
        return language == null ? null : OcrEngine.TryCreateFromLanguage(language);
    }
}
