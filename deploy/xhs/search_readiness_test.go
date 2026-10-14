package xiaohongshu

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/go-rod/rod"
	"github.com/go-rod/rod/lib/launcher"
)

func TestEverydayAISearchReadiness(t *testing.T) {
	bin := os.Getenv("XHS_TEST_BROWSER")
	if bin == "" {
		t.Fatal("XHS_TEST_BROWSER must identify the pinned Chromium")
	}
	control, err := launcher.New().Bin(bin).Headless(true).NoSandbox(true).Launch()
	if err != nil {
		t.Fatal(err)
	}
	b := rod.New().ControlURL(control).MustConnect()
	defer b.MustClose()
	cases := []struct{ name, html, want string }{
		{"delayed-feeds", `<script>window.__INITIAL_STATE__={search:{feeds:{value:[]}}};setTimeout(()=>window.__INITIAL_STATE__.search.feeds.value=[{id:'note'}],300)</script>`, ""},
		{"state-without-results", `<script>window.__INITIAL_STATE__={search:{feeds:{value:[]}}}</script>`, "XHS_SEARCH_TIMEOUT"},
		{"missing-state", `<p>Loading</p>`, "XHS_SEARCH_TIMEOUT"},
		{"login", `<div class="login-container">登录</div>`, "XHS_SEARCH_AUTH_REQUIRED"},
		{"captcha", `<div class="captcha-container">安全验证</div>`, "XHS_SEARCH_CAPTCHA_REQUIRED"},
		{"empty", `<div class="empty-container">暂无搜索结果</div><script>window.__INITIAL_STATE__={search:{feeds:{value:[]}}}</script>`, ""},
		{"hidden-login", `<div class="login-container" style="display:none">登录</div><script>window.__INITIAL_STATE__={search:{feeds:{_value:[{id:'note'}]}}}</script>`, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				w.Write([]byte(tc.html))
			}))
			defer server.Close()
			p := b.MustPage(server.URL)
			defer p.MustClose()
			p.MustWaitLoad()
			err := waitSearchReady(p, 800*time.Millisecond)
			if tc.want == "" {
				if err != nil {
					t.Fatal(err)
				}
			} else if err == nil || err.Error() != tc.want {
				t.Fatalf("want %s, got %v", tc.want, err)
			}
		})
	}
	t.Run("cancellation", func(t *testing.T) {
		p := b.MustPage()
		defer p.MustClose()
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		if err := waitSearchReady(p.Context(ctx), time.Second); err != context.Canceled {
			t.Fatalf("got %v", err)
		}
	})
}
