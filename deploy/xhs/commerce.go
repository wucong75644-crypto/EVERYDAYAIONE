package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"regexp"
	"time"

	"github.com/gin-gonic/gin"
)

var commerceID = regexp.MustCompile(`^[0-9a-f]{24}$`)

type commerceRequest struct {
	SellerID string `json:"seller_id"`
	SKUID    string `json:"sku_id"`
	Page     int    `json:"page"`
	PageSize int    `json:"page_size"`
}

type commerceResponse struct {
	Status int    `json:"status"`
	Body   string `json:"body"`
}

// All destinations and operations are fixed; never expose a browser fetch proxy.
func commerceQuery(req commerceRequest, shop bool) (string, string, any, error) {
	if shop {
		if !commerceID.MatchString(req.SellerID) || req.SKUID != "" || req.Page < 1 || req.Page > 100 || req.PageSize < 1 || req.PageSize > 10 {
			return "", "", nil, errors.New("invalid shop request")
		}
		endpoint := "https://mall.xiaohongshu.com/api/store/guide/components/general/h5"
		body := map[string]any{
			"biz_identity":    map[string]any{"template_id": "shopH5Page", "biz_type": "shop", "scene": "shop_pc"},
			"component_param": map[string]any{"mix_card_component_param": []any{map[string]any{"sort_type": "sales_qty", "page_param": map[string]any{"page": req.Page - 1, "page_size": req.PageSize}}}},
			"common_param":    map[string]any{"seller_id": req.SellerID},
		}
		return endpoint, "POST", body, nil
	}
	if !commerceID.MatchString(req.SKUID) || req.SellerID != "" || req.Page != 0 || req.PageSize != 0 {
		return "", "", nil, errors.New("invalid product request")
	}
	return "https://mall.xiaohongshu.com/api/store/jpd/edith/detail/h5/toc?version=0.0.5&item_id=" + req.SKUID, "GET", nil, nil
}

func commerceData(result commerceResponse) (json.RawMessage, string) {
	if len(result.Body) > 2*1024*1024 {
		return nil, "COMMERCE_CHANGED"
	}
	if result.Status == 401 || result.Status == 403 {
		return nil, "COMMERCE_AUTH_REQUIRED"
	}
	if result.Status != http.StatusOK {
		return nil, "COMMERCE_CHANGED"
	}
	var envelope struct {
		Success bool            `json:"success"`
		Code    int             `json:"code"`
		Data    json.RawMessage `json:"data"`
	}
	if json.Unmarshal([]byte(result.Body), &envelope) != nil {
		return nil, "COMMERCE_CHANGED"
	}
	if envelope.Code == -100 {
		return nil, "COMMERCE_AUTH_REQUIRED"
	}
	if !envelope.Success || len(envelope.Data) == 0 || string(envelope.Data) == "null" {
		return nil, "COMMERCE_CHANGED"
	}
	return envelope.Data, ""
}

func (s *AppServer) commerceShopHandler(c *gin.Context)    { s.commerceHandler(c, true) }
func (s *AppServer) commerceProductHandler(c *gin.Context) { s.commerceHandler(c, false) }

func (s *AppServer) commerceHandler(c *gin.Context, shop bool) {
	var req commerceRequest
	if c.ShouldBindJSON(&req) != nil {
		respondError(c, 400, "INVALID_REQUEST", "商品查询参数无效", nil)
		return
	}
	endpoint, method, body, err := commerceQuery(req, shop)
	if err != nil {
		respondError(c, 400, "INVALID_REQUEST", "商品查询参数无效", nil)
		return
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 45*time.Second)
	defer cancel()
	// Match the existing daemon's browser factory, fingerprint, proxy and account.
	// Parent Agent Reach owns account locking, quota and inter-request admission.
	b := newBrowser()
	defer b.Close()
	page := b.NewPage().Context(ctx)
	defer page.Close()
	if err = page.Navigate(endpoint); err != nil {
		code := "COMMERCE_CHANGED"
		if ctx.Err() != nil {
			code = "COMMERCE_TIMEOUT"
		}
		respondError(c, 502, code, "商品网页未完成加载", nil)
		return
	}
	value, err := page.Eval(`async (endpoint,method,body) => {
  const response = await fetch(endpoint,{method,credentials:'include',redirect:'error',
   headers:{'Content-Type':'application/json'},body:method==='POST'?JSON.stringify(body):undefined});
  const reader=response.body.getReader();let size=0;const chunks=[];
  for(;;){const {done,value}=await reader.read();if(done)break;size+=value.length;
   if(size>2097152){await reader.cancel();throw new Error('response limit');}chunks.push(value);}
  const bytes=new Uint8Array(size);let offset=0;for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
  return {status:response.status,body:new TextDecoder().decode(bytes)};
 }`, endpoint, method, body)
	if err != nil {
		code := "COMMERCE_CHANGED"
		if ctx.Err() != nil {
			code = "COMMERCE_TIMEOUT"
		}
		respondError(c, 502, code, "未取得商品资料", nil)
		return
	}
	var result commerceResponse
	if json.Unmarshal([]byte(value.Value.JSON("", "")), &result) != nil {
		respondError(c, 502, "COMMERCE_CHANGED", "商品响应格式变化", nil)
		return
	}
	data, code := commerceData(result)
	if code != "" {
		respondError(c, 502, code, "未取得可验证商品资料", nil)
		return
	}
	respondSuccess(c, data, "商品读取完成")
}
