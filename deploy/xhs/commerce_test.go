package main

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestEverydayAICommerceQuery(t *testing.T) {
	req := commerceRequest{SellerID: "65263c56796c030001e298d4", Page: 3, PageSize: 10}
	endpoint, method, body, err := commerceQuery(req, true)
	if err != nil || method != "POST" || endpoint != "https://mall.xiaohongshu.com/api/store/guide/components/general/h5" {
		t.Fatal("shop query", err)
	}
	raw, _ := json.Marshal(body)
	var data map[string]any
	json.Unmarshal(raw, &data)
	param := data["component_param"].(map[string]any)["mix_card_component_param"].([]any)[0].(map[string]any)["page_param"].(map[string]any)
	if param["page"] != float64(2) {
		t.Fatal("page must be zero based")
	}
	for _, bad := range []commerceRequest{{SellerID: "https://evil.test", Page: 1, PageSize: 5}, {SellerID: req.SellerID, Page: 0, PageSize: 5}, {SellerID: req.SellerID, Page: 1, PageSize: 11}, {SKUID: "../login"}} {
		if _, _, _, err := commerceQuery(bad, bad.SellerID != ""); err == nil {
			t.Fatal("accepted invalid query")
		}
	}
	if _, _, _, err := commerceQuery(commerceRequest{SKUID: "6967abf98f0f9c0001c2ab3d"}, false); err != nil {
		t.Fatal(err)
	}
}
func TestEverydayAICommerceData(t *testing.T) {
	for _, tc := range []struct{ body, code string }{
		{`{"success":true,"data":{"items":[]}}`, ""},
		{`{"success":false,"code":-100,"data":{}}`, "COMMERCE_AUTH_REQUIRED"},
		{`<html>login</html>`, "COMMERCE_CHANGED"},
		{`{"success":true,"data":null}`, "COMMERCE_CHANGED"},
	} {
		_, code := commerceData(commerceResponse{Status: 200, Body: tc.body})
		if code != tc.code {
			t.Fatalf("got %s expected %s", code, tc.code)
		}
	}
}

func TestEverydayAICommerceRoutesRequireAuthentication(t *testing.T) {
	router := setupRoutes(&AppServer{authToken: "test-only"})
	for _, path := range []string{"/api/v1/commerce/shop", "/api/v1/commerce/product"} {
		recorder := httptest.NewRecorder()
		request := httptest.NewRequest("POST", path, strings.NewReader("{}"))
		request.Header.Set("Content-Type", "application/json")
		router.ServeHTTP(recorder, request)
		if recorder.Code != 401 {
			t.Fatalf("unauthenticated commerce status %d", recorder.Code)
		}
		recorder = httptest.NewRecorder()
		request = httptest.NewRequest("POST", path, strings.NewReader("{}"))
		request.Header.Set("Content-Type", "application/json")
		request.Header.Set("Authorization", "Bearer test-only")
		router.ServeHTTP(recorder, request)
		if recorder.Code != 400 {
			t.Fatalf("invalid input commerce status %d", recorder.Code)
		}
	}
}
