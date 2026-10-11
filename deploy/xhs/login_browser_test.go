package main

import (
 "context"
 "sync"
 "testing"
)

// Pending scans must be checked without launching Chromium (including parallel polls).
func TestEverydayAIPendingScanStatus(t *testing.T) {
 s := NewXiaohongshuService()
 seq := s.logins.start(func(){})
 var wg sync.WaitGroup
 for i := 0; i < 50; i++ {
  wg.Add(1)
  go func() {
   defer wg.Done()
   result, err := s.CheckLoginStatus(context.Background())
   if err != nil || result.IsLoggedIn { t.Errorf("pending scan: result=%v err=%v", result, err) }
  }()
 }
 wg.Wait()
 if _, err := s.GetLoginQrcode(context.Background()); err == nil { t.Error("must not replace a pending QR browser") }
 s.logins.finish(seq)
 if s.logins.pending() { t.Error("finished scan remained pending") }
}
