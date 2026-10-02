# 系統介面與安全介入圖庫

本頁將兩類畫面分開呈現：第一類是本人負責之風險偵測與安全介入機制的實機畫面；第二類是團隊整體系統介面，用來交代本機制實際整合進何種產品情境。團隊介面不代表全部由本人開發。

## 風險等級與雙方介入畫面

下列五張畫面取自同一段連續對話，左側為寄件方、右側為收件方，對應成果進度報告中的圖（四）至圖（七）及圖（十）。

### 觀察級

寄件方不顯示提示，避免風險剛浮現時造成寒蟬效應；收件方看到可隨時封鎖或檢舉的環境提醒。

![觀察級風險下的寄件方與收件方畫面](../assets/screenshots/intervention/observation.png)

### 警告級

寄件方收到依主要風險維度產生的反思提示；收件方看到保護卡片與「沒問題／有問題」回饋按鈕。

![警告級風險下的寄件方與收件方畫面](../assets/screenshots/intervention/warning.png)

### 限制級

寄件方看到暫停提示並進入 60 秒冷卻；收件方看到加強保護說明、封鎖、檢舉、停止對話及補充說明等行動選項。

![限制級風險下的寄件方與收件方畫面](../assets/screenshots/intervention/restricted.png)

### 封鎖級

寄件方的訊息不送出，帳號功能暫時受限；收件方收到攔截通知與封鎖、檢舉或結束對話等選項。

![封鎖級風險下的寄件方與收件方畫面](../assets/screenshots/intervention/blocked.png)

### 已處置豁免與持續保護

先前已完成介入且沒有新的風險增量時，系統不為同一事件重複處罰寄件方，但保留累積風險狀態；收件方的保護卡片與行動選項仍持續顯示。

![已處置豁免觸發時的寄件方與收件方畫面](../assets/screenshots/intervention/sanction-exemption.png)

## 團隊系統介面

以下畫面用來呈現風險機制所整合的交友軟體原型及主視覺角色「阿月（A-Yue）」。

<table>
  <tr>
    <td align="center"><a href="../assets/screenshots/system/matching-home.png"><img src="../assets/screenshots/system/matching-home.png" width="270" alt="配對首頁"></a><br>配對與牽線首頁</td>
    <td align="center"><a href="../assets/screenshots/system/agent-guidance-chat.png"><img src="../assets/screenshots/system/agent-guidance-chat.png" width="270" alt="阿月互動引導"></a><br>阿月互動引導</td>
    <td align="center"><a href="../assets/screenshots/system/match-search-progress.png"><img src="../assets/screenshots/system/match-search-progress.png" width="270" alt="媒人搜尋進度"></a><br>媒人搜尋與進度</td>
  </tr>
  <tr>
    <td align="center"><a href="../assets/screenshots/system/profile.png"><img src="../assets/screenshots/system/profile.png" width="270" alt="個人頁面"></a><br>個人頁面</td>
    <td align="center"><a href="../assets/screenshots/system/settings.png"><img src="../assets/screenshots/system/settings.png" width="270" alt="設定頁面"></a><br>設定頁面</td>
    <td align="center"><a href="../assets/screenshots/system/loading.png"><img src="../assets/screenshots/system/loading.png" width="270" alt="載入畫面"></a><br>載入畫面</td>
  </tr>
</table>

