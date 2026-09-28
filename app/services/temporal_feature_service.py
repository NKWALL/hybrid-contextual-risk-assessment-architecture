from datetime import datetime, timezone
from typing import List
from app.models.schemas import TemporalFeatures, Message

class TemporalFeatureService:
    @staticmethod
    def calculate(current_content: str, current_sender: str, history: List[Message]) -> TemporalFeatures:
        """
        根據歷史訊息與當前訊息計算行為特徵。
        後端為唯一 Source of Truth。
        """
        now = datetime.now(timezone.utc)
        current_len = len(current_content)
        
        # 1. 基礎秒數與狀態初始化
        reply_latency_seconds = None
        idle_time_seconds = None
        
        if history:
            # history 順序為 [舊 -> 新]，history[-1] 是最新的歷史訊息
            last_msg = history[-1]
            last_ts = datetime.fromisoformat(last_msg.timestamp.replace('Z', '+00:00'))
            
            # Idle Time: 聊天室物理間隔 (不論是誰發的)
            idle_time_seconds = int((now - last_ts).total_seconds())
            
            # Reply Latency: 只有當上一則是「對方」發的，我方才算回覆
            if last_msg.sender != current_sender:
                reply_latency_seconds = idle_time_seconds

        # 2. unreplied_count & consecutive_char_count
        unreplied_count = 1 
        consecutive_char_count = current_len
        for msg in reversed(history):
            if msg.sender == current_sender:
                unreplied_count += 1
                consecutive_char_count += len(msg.content)
            else:
                break

        # 3. message_burst_count (短時間連續發送)
        message_burst_count = 1
        current_ref_time = now
        for msg in reversed(history):
            if msg.sender == current_sender:
                msg_ts = datetime.fromisoformat(msg.timestamp.replace('Z', '+00:00'))
                interval = (current_ref_time - msg_ts).total_seconds()
                if interval <= 5:
                    message_burst_count += 1
                    current_ref_time = msg_ts
                else:
                    break
            else:
                break

        # 4. 近期視窗統計 (視窗：最近 20 則 + 目前 1 則)
        window_msgs = history[-20:] if len(history) > 20 else history
        all_count = len(window_msgs) + 1
        sender_count = sum(1 for m in window_msgs if m.sender == current_sender) + 1
        all_volume = sum(len(m.content) for m in window_msgs) + current_len
        sender_volume = sum(len(m.content) for m in window_msgs if m.sender == current_sender) + current_len
        
        message_ratio = round(sender_count / all_count, 4)
        volume_ratio = round(sender_volume / all_volume, 4) if all_volume > 0 else 1.0
        avg_chars_per_message = round(sender_volume / sender_count, 2)

        # 5. frequency (近期發送密度) —— 【核心修正版】
        # 定義：以目前訊息為中心的 Trailing Sender Sequence 密度
        normalized_frequency = 0.1
        sender_msgs = [m for m in window_msgs if m.sender == current_sender]
        
        if sender_msgs:
            # 建立包含目前的 timestamp 序列 (新 -> 舊)
            ts_seq = [now] + [datetime.fromisoformat(m.timestamp.replace('Z', '+00:00')) for m in reversed(sender_msgs)]
            
            # 計算最近的相鄰間隔 (最多取最近 3 個間隔以反映「當前」密度)
            intervals = []
            for i in range(min(3, len(ts_seq) - 1)):
                interval = (ts_seq[i] - ts_seq[i+1]).total_seconds()
                intervals.append(interval)
            
            if intervals:
                avg_interval = sum(intervals) / len(intervals)
                # 映射：10s(1.0) -> 180s(0.0)
                # 公式：1.0 - (間隔越長扣越多)
                score = 1.0 - (avg_interval - 10) / 170.0
                normalized_frequency = max(0.0, min(1.0, score))

        # 6. 舊有相容 latency (映射自 reply_latency_seconds)
        normalized_latency = 0.0
        if reply_latency_seconds is not None:
            normalized_latency = min(1.0, reply_latency_seconds / 1800.0)

        return TemporalFeatures(
            reply_latency_seconds=reply_latency_seconds,
            idle_time_seconds=idle_time_seconds,
            unreplied_count=unreplied_count,
            consecutive_char_count=consecutive_char_count,
            message_ratio=message_ratio,
            volume_ratio=volume_ratio,
            avg_chars_per_message=avg_chars_per_message,
            latency=normalized_latency,
            frequency=round(normalized_frequency, 4),
            message_burst_count=message_burst_count
        )
