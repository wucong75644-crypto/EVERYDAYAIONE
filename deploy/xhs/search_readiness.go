package xiaohongshu

import (
	"context"
	"fmt"
	"time"

	"github.com/go-rod/rod"
	"github.com/sirupsen/logrus"
)

// Search readiness is result-specific: page stability and the global state object
// do not establish whether results loaded, or whether a login/challenge intervened.
const searchReadinessJS = `() => {
    const visible = selector => [...document.querySelectorAll(selector)].some(el =>
        el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
    if (/\/security|\/captcha/.test(location.pathname) ||
        visible('#captcha-iframe, .captcha-container, .verify-container')) return 'captcha';
    if (/\/login(?:\/|$)/.test(location.pathname) || visible('.login-container')) return 'login';
    const feeds = window.__INITIAL_STATE__?.search?.feeds;
    const data = feeds?.value ?? feeds?._value;
    if (Array.isArray(data) && data.length > 0) return 'ready';
    const empty = [...document.querySelectorAll('.search-empty, .empty-container, .no-result')]
        .some(el => el.getClientRects().length && /暂无|没有找到|无相关|无搜索结果/.test(el.innerText));
    if (Array.isArray(data) && data.length === 0 && empty) return 'ready';
    return window.__INITIAL_STATE__ === undefined ? 'state_missing' : 'results_pending';
}`

func waitSearchReady(page *rod.Page, timeout time.Duration) error {
	ctx, cancel := context.WithTimeout(page.GetContext(), timeout)
	defer cancel()
	page = page.Context(ctx)
	lastStage := "not_evaluated"
	for {
		if err := ctx.Err(); err != nil {
			if page.GetContext().Err() != nil && err == context.Canceled {
				return err
			}
			logrus.Warnf("search readiness timeout stage=%s", lastStage)
			return fmt.Errorf("XHS_SEARCH_TIMEOUT")
		}
		result, err := page.Eval(searchReadinessJS)
		if err == nil {
			lastStage = result.Value.Str()
			switch lastStage {
			case "ready":
				return nil
			case "login":
				return fmt.Errorf("XHS_SEARCH_AUTH_REQUIRED")
			case "captcha":
				return fmt.Errorf("XHS_SEARCH_CAPTCHA_REQUIRED")
			}
		}
		// Navigation can briefly invalidate the JS execution context. Poll within
		// the same bound; never restart a search or return a false empty result.
		timer := time.NewTimer(200 * time.Millisecond)
		select {
		case <-ctx.Done():
			timer.Stop()
		case <-timer.C:
		}
	}
}
