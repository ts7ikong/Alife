import cv2
import numpy as np
from PIL import Image

def convert_greenscreen_to_transparent_gif(video_path, output_gif_path, duration=40):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print("错误：无法打开视频文件")
        return

    frames = []
    print("正在处理绿幕抠像...")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        # 收窄绿色范围，减少误判
        # H: 40-80 比之前的 35-85 更严格
        # S: 提高到 60，排除低饱和度的浅色区域（皮肤/白色衣物不会被抠）
        # V: 提高到 60，排除过暗区域
        lower_green = np.array([40, 60, 60])
        upper_green = np.array([80, 255, 255])

        mask = cv2.inRange(hsv, lower_green, upper_green)

        # 形态学操作：先腐蚀去掉人物上的绿色噪点，再膨胀还原绿幕区域
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)  # 消除人物区域的小绿点
        mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel, iterations=1)  # 稍微扩展绿幕边缘，防止绿边残留

        # 对遮罩边缘做高斯模糊，实现羽化效果，避免人物边缘有锯齿硬边
        mask_blurred = cv2.GaussianBlur(mask, (5, 5), 0)

        # 反转：人物=255(不透明)，绿幕=0(透明)
        alpha = cv2.bitwise_not(mask_blurred)

        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgba = np.dstack((frame_rgb, alpha))
        pil_img = Image.fromarray(rgba, "RGBA")
        frames.append(pil_img)

    cap.release()

    if frames:
        print(f"处理完成，正在生成 GIF（共 {len(frames)} 帧）...")
        frames[0].save(
            output_gif_path,
            save_all=True,
            append_images=frames[1:],
            duration=duration,
            loop=0,
            disposal=2
        )
        print(f"成功！已保存至: {output_gif_path}")
    else:
        print("错误：未提取到任何有效帧")

if __name__ == "__main__":
    video_path = r"C:\Users\tsuik\Desktop\WorkingPet\video.mp4"
    output_gif_path = "manei_coding_transparent.gif"
    convert_greenscreen_to_transparent_gif(video_path, output_gif_path)