#!/usr/bin/env python3
"""快麦开放平台API文档完整爬取脚本"""

import re
import time
import json
import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote
from pathlib import Path


# 所有需要爬取的API文档URL（从sitemap提取）
API_DOC_URLS = [
    # === API对接说明 ===
    "https://open.kuaimai.com/docs/api/API%E5%AF%B9%E6%8E%A5%E8%AF%B4%E6%98%8E/%E6%8E%A5%E5%85%A5%E6%8C%87%E5%8D%97/",
    "https://open.kuaimai.com/docs/api/API%E5%AF%B9%E6%8E%A5%E8%AF%B4%E6%98%8E/API%E8%B0%83%E7%94%A8%E6%96%B9%E6%B3%95%E8%AF%A6%E8%A7%A3/",
    "https://open.kuaimai.com/docs/api/API%E5%AF%B9%E6%8E%A5%E8%AF%B4%E6%98%8E/API%E9%94%99%E8%AF%AF%E7%A0%81%E8%A7%A3%E9%87%8A/",
    "https://open.kuaimai.com/docs/api/API%E5%AF%B9%E6%8E%A5%E8%AF%B4%E6%98%8E/%E9%80%9A%E7%94%A8%E5%AD%97%E6%AE%B5%E8%AF%B4%E6%98%8E/",
    "https://open.kuaimai.com/docs/api/API%E5%AF%B9%E6%8E%A5%E8%AF%B4%E6%98%8E/%E5%BF%AB%E9%80%92%E7%BC%96%E7%A0%81%E5%AF%B9%E5%BA%94%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E5%AF%B9%E6%8E%A5%E8%AF%B4%E6%98%8E/%E8%81%94%E7%B3%BB%E6%88%91%E4%BB%AC/",
    "https://open.kuaimai.com/docs/api/API%E5%AF%B9%E6%8E%A5%E8%AF%B4%E6%98%8E/%E5%B9%B3%E5%8F%B0%E7%BC%96%E7%A0%81%E5%AF%B9%E5%BA%94%E8%A1%A8/",
    # === API文档 - 基础 ===
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%9F%BA%E7%A1%80/%E4%BB%93%E5%BA%93%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%9F%BA%E7%A1%80/%E5%88%86%E9%94%80%E5%95%86%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%9F%BA%E7%A1%80/%E5%88%B7%E6%96%B0%E4%BC%9A%E8%AF%9D%E5%BF%85%E6%8E%A5/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%9F%BA%E7%A1%80/%E5%AE%A2%E6%88%B7%E5%9F%BA%E7%A1%80%E8%B5%84%E6%96%99%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%9F%BA%E7%A1%80/%E5%BA%97%E9%93%BA%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%9F%BA%E7%A1%80/%E6%96%B0%E5%A2%9E%E4%BF%AE%E6%94%B9%E5%AE%A2%E6%88%B7%E5%9F%BA%E6%9C%AC%E4%BF%A1%E6%81%AF/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%9F%BA%E7%A1%80/%E8%8E%B7%E5%8F%96%E5%85%AC%E5%8F%B8%E4%B8%8B%E7%9A%84%E6%A0%87%E7%AD%BE%E5%88%97%E8%A1%A8/",
    # === API文档 - 商品 ===
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9F%A5%E8%AF%A2%E5%95%86%E5%93%81%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9F%A5%E8%AF%A2%E5%8D%95%E4%B8%AA%E5%95%86%E5%93%81%E6%98%8E%E7%BB%86/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9F%A5%E8%AF%A2%E5%95%86%E5%93%81SKU%E4%BF%A1%E6%81%AF/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9F%A5%E8%AF%A2%E5%95%86%E5%93%81SKU%E5%88%97%E8%A1%A8V2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9F%A5%E8%AF%A2%E5%BA%93%E5%AD%98%E7%8A%B6%E6%80%81/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E4%BF%AE%E6%94%B9%E6%96%B0%E5%A2%9E%E6%99%AE%E9%80%9A%E5%95%86%E5%93%81/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E4%BF%AE%E6%94%B9%E6%96%B0%E5%A2%9E%E5%95%86%E5%93%81V2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E4%BF%AE%E6%94%B9%E5%AE%9E%E9%99%85%E5%BA%93%E5%AD%98/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E4%BF%AE%E6%94%B9%E8%99%9A%E6%8B%9F%E5%BA%93%E5%AD%98/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%89%B9%E9%87%8F%E4%BF%AE%E6%94%B9%E8%99%9A%E6%8B%9F%E5%BA%93%E5%AD%98/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9F%A5%E8%AF%A2%E8%99%9A%E6%8B%9F%E4%BB%93/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9F%A5%E8%AF%A2%E5%95%86%E5%93%81%E5%87%BA%E5%85%A5%E5%BA%93%E8%AE%B0%BD%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9F%A5%E8%AF%A2%E5%95%86%E5%93%81%E5%88%86%E7%B1%BB%E4%BF%A1%E6%81%AF/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9F%A5%E8%AF%A2%E5%95%86%E5%93%81%E5%93%81%E7%89%8C%E4%BF%A1%E6%81%AF/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9F%A5%E8%AF%A2%E5%95%86%E5%93%81%E6%A0%87%E7%AD%BE/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9F%A5%E8%AF%A2%E5%95%86%E5%93%81%E7%B1%BB%E7%9B%AE%E4%BF%A1%E6%81%AF/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%96%B0%E5%A2%9E%E5%95%86%E5%93%81%E5%88%86%E7%B1%BB/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%96%B0%E5%A2%9E%E5%95%86%E5%93%81%E6%A0%87%E7%AD%BE/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%96%B0%E5%A2%9E%E5%95%86%E5%93%81%E7%B1%BB%E7%9B%AE/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E8%AE%BE%E7%BD%AE%E5%95%86%E5%93%81%E6%A0%87%E7%AD%BE/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%96%B0%E5%A2%9E%E4%BF%AE%E6%94%B9%E5%95%86%E5%93%81%E5%93%81%E7%89%8C/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%AF%BC%E5%85%A5%E5%B9%B3%E5%8F%B0%E5%95%86%E5%93%81%E4%BF%A1%E6%81%AF/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9F%A5%E8%AF%A2%E4%BB%93%E5%BA%93%E5%8F%8A%E5%95%86%E5%93%81%E5%BA%93%E5%AD%98%E4%BF%A1%E6%81%AF/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%95%86%E5%93%81%E5%AF%B9%E5%BA%94%E5%85%B3%E7%B3%BB%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%95%86%E5%93%81%E5%AF%B9%E5%BA%94%E5%85%B3%E7%B3%BB%E6%9F%A5%E8%AF%A2%EF%BC%88%E6%8C%89%E5%95%86%E5%93%81ID%EF%BC%89/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%95%86%E5%93%81%E5%A4%9A%E7%A0%81%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%95%86%E5%93%81%E5%8E%86%E5%8F%B2%E6%88%90%E6%9C%AC%E4%BB%B7%E7%9A%84%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9B%B4%E6%96%B0%E5%95%86%E5%93%81%E5%8E%86%E5%8F%B2%E6%88%90%E6%9C%AC%E4%BB%B7/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%95%86%E5%93%81%E7%B1%BB%E5%9E%8B%E8%BD%AC%E6%8D%A2%E6%AC%BE%E7%BB%B5%E5%BA%A6/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%B7%BB%E5%8A%A0%E5%95%86%E5%93%81%E4%BE%9B%E5%BA%94%E5%95%86%E5%85%B3%E7%B3%BBV2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E4%BF%AE%E6%94%B9%E5%95%86%E5%93%81%E4%BE%9B%E5%BA%94%E5%95%86%E5%85%B3%E7%B3%BBV2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%88%A0%E9%99%A4%E5%95%86%E5%93%81%E4%BE%9B%E5%BA%94%E5%95%86%E5%85%B3%E7%B3%BBV2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%95%86%E5%93%81%E5%85%B3%E8%81%94%E4%BE%9B%E5%BA%94%E5%95%86%E4%BF%A1%E6%81%AF%E6%9B%B4%E6%96%B0V2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9F%A5%E8%AF%A2%E5%95%86%E5%93%81%E5%85%B3%E8%81%94%E4%BE%9B%E5%BA%94%E5%95%86%E4%BF%A1%E6%81%AFV2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E6%9F%A5%E8%AF%A2%E5%A4%9A%E4%B8%AA%E5%95%86%E5%93%81%E4%BF%A1%E6%81%AFV2/",
    # 商品V1.0版本
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%95%86%E5%93%81V1.0%E7%89%88%E6%9C%AC/%E6%B7%BB%E5%8A%A0%E5%95%86%E5%93%81%E4%BE%9B%E5%BA%94%E5%95%86%E5%85%B3%E7%B3%BBV1.0/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%95%86%E5%93%81V1.0%E7%89%88%E6%9C%AC/%E4%BF%AE%E6%94%B9%E5%95%86%E5%93%81%E4%BE%9B%E5%BA%94%E5%95%86%E5%85%B3%E7%B3%BBV1.0/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%95%86%E5%93%81V1.0%E7%89%88%E6%9C%AC/%E5%88%A0%E9%99%A4%E5%95%86%E5%93%81%E4%BE%9B%E5%BA%94%E5%95%86%E5%85%B3%E7%B3%BBV1.0/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%95%86%E5%93%81V1.0%E7%89%88%E6%9C%AC/%E5%95%86%E5%93%81%E5%85%B3%E8%81%94%E4%BE%9B%E5%BA%94%E5%95%86%E4%BF%A1%E6%81%AF%E6%9B%B4%E6%96%B0V1.0/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%95%86%E5%93%81V1.0%E7%89%88%E6%9C%AC/%E6%9F%A5%E8%AF%A2%E5%95%86%E5%93%81SKU%E5%88%97%E8%A1%A8V1.0/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%95%86%E5%93%81V1.0%E7%89%88%E6%9C%AC/%E6%9F%A5%E8%AF%A2%E5%95%86%E5%93%81%E5%85%B3%E8%81%94%E4%BE%9B%E5%BA%94%E5%95%86%E4%BF%A1%E6%81%AFV1.0/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%95%86%E5%93%81/%E5%95%86%E5%93%81V1.0%E7%89%88%E6%9C%AC/%E6%9F%A5%E8%AF%A2%E5%A4%9A%E4%B8%AA%E5%95%86%E5%93%81%E4%BF%A1%E6%81%AFV1.0/",
    # === API文档 - 交易 ===
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E8%AE%A2%E5%8D%95%E6%9F%A5%E8%AF%A2%E9%9D%9E%E6%B7%98%E7%B3%BB%E6%8B%BC%E5%A4%9A%E5%A4%9A/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E9%94%80%E5%94%AE%E5%87%BA%E5%BA%93%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E9%94%80%E5%94%AE%E5%87%BA%E5%BA%93%E5%8D%95%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E5%88%9B%E5%BB%BA%E8%87%AA%E5%BB%BA%E5%B9%B3%E5%8F%B0%E8%AE%A2%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E5%88%9B%E5%BB%BA%E7%B3%BB%E7%BB%9F%E6%89%8B%E5%B7%A5%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E4%B8%8A%E4%BC%A0%E5%8F%91%E8%B4%A7/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E4%B8%8A%E4%BC%A0%E5%A4%87%E6%B3%A8%E4%B8%8E%E6%97%97%E5%B8%9C/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E4%BF%AE%E6%94%B9%E8%AE%A2%E5%8D%95%E4%BB%93%E5%BA%93/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E4%BF%AE%E6%94%B9%E8%AE%A2%E5%8D%95%E5%8D%96%E5%AE%B6%E5%A4%87%E6%B3%A8%E4%B8%8E%E6%97%97%E5%B8%9C/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E4%BF%AE%E6%94%B9%E8%AE%A2%E5%8D%95%E5%95%86%E5%93%81%E5%A4%87%E6%B3%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E4%BF%AE%E6%94%B9%E8%AE%A2%E5%8D%95%E6%94%B6%E8%B4%A7%E5%9C%B0%E5%9D%80/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E6%89%B9%E9%87%8F%E4%BF%AE%E6%94%B9%E8%AE%A2%E5%8D%95%E5%95%86%E5%93%81/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E6%89%B9%E9%87%8F%E4%BF%AE%E6%94%B9%E8%AE%A2%E5%8D%95%E6%A0%87%E7%AD%BE/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E8%AE%A2%E5%8D%95%E6%93%8D%E4%BD%9C%E6%97%A5%E5%BF%97/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E8%AE%A2%E5%8D%95%E4%BD%9C%E5%BA%9F%E6%8E%A5%E5%8F%A3/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E8%AE%A2%E5%8D%95%E6%8C%82%E8%B5%B7/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E8%AE%A2%E5%8D%95%E8%A7%A3%E6%8C%82/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E8%AE%A2%E5%8D%95%E5%8F%91%E8%B4%A7%E6%8B%A6%E6%88%AA%E6%8E%A5%E5%8F%A3/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E6%9B%B4%E6%96%B0%E8%AE%A2%E5%8D%95%E7%89%A9%E6%B5%81%E6%A8%A1%E6%9D%BF/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E7%94%A8%E6%88%B7%E7%89%A9%E6%B5%81%E5%85%AC%E5%8F%B8%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E7%94%A8%E6%88%B7%E7%89%A9%E6%B5%81%E6%A8%A1%E6%9D%BF%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E8%8E%B7%E5%8F%96%E7%89%A9%E6%B5%81%E5%8D%95%E5%8F%B7/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E5%A4%9A%E5%BF%AB%E9%80%92%E5%8D%95%E5%8F%B7%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E5%8D%B3%E5%85%A5%E5%8D%B3%E5%87%BA%E5%8C%B9%E9%85%8D/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E5%8C%85%E8%A3%85%E9%AA%8C%E8%B4%A7/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E6%96%B0%E5%A2%9E%E5%95%86%E5%93%81%E5%94%AF%E4%B8%80%E7%A0%81/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E5%95%86%E5%93%81%E5%94%AF%E4%B8%80%E7%A0%81%E6%9B%B4%E6%96%B0/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E6%9F%A5%E8%AF%A2%E5%94%AF%E4%B8%80%E7%A0%81/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E6%A0%A1%E9%AA%8C%E6%B3%A2%E6%AC%A1%E5%94%AF%E4%B8%80%E7%A0%81/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E8%AE%A2%E5%8D%95%E5%94%AF%E4%B8%80%E7%A0%81%E6%94%B6%E8%B4%A7/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E6%B3%A2%E6%AC%A1%E4%BF%A1%E6%81%AF%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E6%B3%A2%E6%AC%A1%E5%88%86%E6%8B%A3%E4%BF%A1%E6%81%AF%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E6%B3%A2%E6%AC%A1%E6%89%8B%E5%8A%A8%E6%8B%A3%E9%80%89/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BA%A4%E6%98%93/%E6%B3%A2%E6%AC%A1%E6%92%AD%E7%A7%8D%E5%9B%9E%E4%BC%A0/",
    # === API文档 - 售后 ===
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E5%94%AE%E5%90%8E%E5%B7%A5%E5%8D%95%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E5%88%9B%E5%BB%BA%E5%94%AE%E5%90%8E%E5%B7%A5%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E4%BD%9C%E5%BA%9F%E5%94%AE%E5%90%8E%E5%B7%A5%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E4%BF%AE%E6%94%B9%E5%94%AE%E5%90%8E%E5%B7%A5%E5%8D%95%E5%A4%87%E6%B3%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E5%94%AE%E5%90%8E%E5%B7%A5%E5%8D%95%E9%80%80%E8%B4%A7%E5%85%A5%E4%BB%93/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E8%A7%A3%E5%86%B3%E5%94%AE%E5%90%8E%E5%B7%A5%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E6%89%B9%E9%87%8F%E4%BF%AE%E6%94%B9%E5%94%AE%E5%90%8E%E7%B1%BB%E5%9E%8B/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E6%9B%B4%E6%96%B0%E5%94%AE%E5%90%8E%E5%8D%95%E5%94%AE%E5%90%8E%E8%AF%B4%E6%98%8E/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E6%9B%B4%E6%96%B0%E5%94%AE%E5%90%8E%E5%B7%A5%E5%8D%95%E6%A0%87%E8%AE%B0/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E6%9B%B4%E6%96%B0%E5%B7%A5%E5%8D%95%E5%B9%B3%E5%8F%B0%E5%AE%9E%E9%80%80%E9%87%91%E9%A2%9D/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E6%9B%B4%E6%96%B0%E5%B7%A5%E5%8D%95%E9%80%80%E8%B4%A7%E5%BF%AB%E9%80%92%E4%BF%A1%E6%81%AF/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E6%9F%A5%E8%AF%A2%E5%94%AE%E5%90%8E%E5%B7%A5%E5%8D%95%E6%97%A5%E5%BF%97/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E7%99%BB%E8%AE%B0%E8%A1%A5%E6%AC%BE%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E9%94%80%E9%80%80%E5%85%A5%E5%BA%93%E5%8D%95%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E7%BB%B4%E4%BF%AE%E5%8D%95%E5%88%97%E8%A1%A8%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E7%BB%B4%E4%BF%AE%E5%8D%95%E8%AF%A6%E6%83%85%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E7%BB%B4%E4%BF%AE%E5%8D%95%E5%A4%84%E7%90%86/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E7%BB%B4%E4%BF%AE%E5%8D%95%E4%BF%AE%E6%94%B9%E8%B4%B9%E7%94%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%94%AE%E5%90%8E/%E7%BB%B4%E4%BF%AE%E5%8D%95%E4%BB%98%E6%AC%BE/",
    # === API文档 - 仓储 ===
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%96%B0%E5%A2%9E%E5%85%B6%E4%BB%96%E5%85%A5%E5%BA%93%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E5%85%B6%E4%BB%96%E5%85%A5%E5%BA%93%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E5%85%B6%E4%BB%96%E5%85%A5%E5%BA%93%E5%8D%95%E6%98%8E%E7%BB%86/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E5%AE%A1%E6%A0%B8%E5%85%B6%E4%BB%96%E5%85%A5%E5%BA%93%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E4%BD%9C%E5%BA%9F%E5%85%B6%E4%BB%96%E5%85%A5%E5%BA%93%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%96%B0%E5%A2%9E%E5%85%B6%E4%BB%96%E5%87%BA%E5%BA%93%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E5%85%B6%E4%BB%96%E5%87%BA%E5%BA%93%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E5%85%B6%E4%BB%96%E5%87%BA%E5%BA%93%E5%8D%95%E6%98%8E%E7%BB%86/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E5%AE%A1%E6%A0%B8%E5%85%B6%E4%BB%96%E5%87%BA%E5%BA%93%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E4%BD%9C%E5%BA%9F%E5%85%B6%E4%BB%96%E5%87%BA%E5%BA%93%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%96%B0%E5%A2%9E%E8%B0%83%E6%8B%A8%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%96%B0%E5%A2%9E%E5%AE%8C%E6%88%90%E7%9A%84%E8%B0%83%E6%8B%A8%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E8%B0%83%E6%8B%A8%E5%8D%95%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E8%B0%83%E6%8B%A8%E6%98%8E%E7%BB%86%E6%8E%A5%E5%8F%A3/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E8%B0%83%E6%8B%A8%E5%87%BA%E5%BA%93%E5%8D%95%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E8%B0%83%E6%8B%A8%E5%87%BA%E5%BA%93%E5%8D%95%E6%98%8E%E7%BB%86/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E8%B0%83%E6%8B%A8%E5%87%BA%E5%BA%93%E5%8D%95%E7%9B%B4%E6%8E%A5%E5%87%BA%E5%BA%93/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E8%B0%83%E6%8B%A8%E5%85%A5%E5%BA%93%E5%8D%95%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E8%B0%83%E6%8B%A8%E5%85%A5%E5%BA%93%E5%8D%95%E6%98%8E%E7%BB%86/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E8%B0%83%E6%8B%A8%E5%85%A5%E5%BA%93%E5%8D%95%E6%94%B6%E8%B4%A7/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E7%9B%98%E7%82%B9%E5%8D%95%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E7%9B%98%E7%82%B9%E5%8D%95%E6%98%8E%E7%BB%86/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E7%9B%98%E7%82%B9%E5%8D%95%E5%BA%93%E5%AD%98%E7%9B%98%E7%82%B9/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E5%8A%A0%E5%B7%A5%E5%8D%95%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E5%8A%A0%E5%B7%A5%E5%8D%95%E6%98%8E%E7%BB%86/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%96%B0%E5%BB%BA%E4%BF%AE%E6%94%B9%E4%B8%8B%E6%9E%B6%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E4%B8%8B%E6%9E%B6%E5%8D%95%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9F%A5%E8%AF%A2%E4%B8%8B%E6%9E%B6%E5%8D%95%E6%98%8E%E7%BB%86/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E4%B8%8B%E6%9E%B6%E5%8D%95%E4%B8%8B%E6%9E%B6/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E6%9A%82%E5%AD%98%E5%8C%BA%E6%89%B9%E9%87%8F%E4%B8%8A%E6%9E%B6/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E5%95%86%E5%93%81%E6%89%B9%E6%AC%A1%E6%95%88%E6%9C%9F%E5%BA%93%E5%AD%98%E6%9F%A5%E8%AF%A2%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E8%B4%A7%E4%BD%8D%E5%BA%93%E5%AD%98%E6%9F%A5%E8%AF%A2%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E8%B4%A7%E4%BD%8D%E5%BA%93%E5%AD%98%E5%88%A0%E9%99%A4%E6%95%B0%E6%8D%AE%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E4%BB%93%E5%82%A8/%E8%B4%A7%E4%BD%8D%E8%BF%9B%E5%87%BA%E8%AE%B0%E5%BD%95%E6%9F%A5%E8%AF%A2/",
    # === API文档 - 采购 ===
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E6%96%B0%E5%A2%9E%E9%87%87%E8%B4%AD%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E6%96%B0%E5%BB%BA%E4%BF%AE%E6%94%B9%E9%87%87%E8%B4%AD%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E9%87%87%E8%B4%AD%E5%8D%95%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E9%87%87%E8%B4%AD%E5%8D%95%E8%AF%A6%E6%83%85/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E9%87%87%E8%B4%AD%E5%8D%95%E7%8A%B6%E6%80%81%E6%9B%B4%E6%96%B0/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E9%87%87%E8%B4%AD%E5%8D%95%E5%8F%8D%E5%AE%A1%E6%A0%B8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E6%9B%B4%E6%96%B0%E9%87%87%E8%B4%AD%E5%8D%95%E7%89%B9%E6%AE%8A%E5%AD%97%E6%AE%B5/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E6%94%B6%E8%B4%A7%E5%8D%95%E6%96%B0%E5%A2%9E%E4%BF%AE%E6%94%B9/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E6%94%B6%E8%B4%A7%E5%8D%95%E6%9F%A5%E8%AF%A2%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E6%94%B6%E8%B4%A7%E5%8D%95%E8%AF%A6%E6%83%85/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E6%94%B6%E8%B4%A7%E5%8D%95%E6%94%B6%E8%B4%A7/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E6%94%B6%E8%B4%A7%E5%8D%95%E6%89%93%E5%9B%9E/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E6%94%B6%E8%B4%A7%E5%8D%95%E4%BD%9C%E5%BA%9F/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E9%87%87%E9%80%80%E5%8D%95%E4%BF%9D%E5%AD%98/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E9%87%87%E9%80%80%E5%8D%95%E6%9F%A5%E8%AF%A2%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E9%87%87%E9%80%80%E5%8D%95%E8%AF%A6%E6%83%85/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E9%87%87%E9%80%80%E5%8D%95%E5%87%BA%E5%BA%93/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E9%87%87%E9%80%80%E5%8D%95%E4%BD%9C%E5%BA%9F/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E6%9F%A5%E8%AF%A2%E4%BE%9B%E5%BA%94%E5%95%86%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E6%96%B0%E5%BB%BA%E4%BF%AE%E6%94%B9%E4%BE%9B%E5%BA%94%E5%95%86/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E6%9F%A5%E8%AF%A2%E4%B8%8A%E6%9E%B6%E5%8D%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E6%9F%A5%E8%AF%A2%E4%B8%8A%E6%9E%B6%E5%8D%95%E8%AF%A6%E6%83%85/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E4%B8%8A%E6%9E%B6%E5%8D%95%E4%B8%8A%E6%9E%B6/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E9%87%87%E8%B4%AD%E5%BF%AB%E9%80%9F%E6%94%B6%E8%B4%A7%E5%8D%B3%E5%85%A5%E5%8D%B3%E5%87%BA/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E8%AE%A1%E7%AE%97%E5%B7%B2%E5%94%AE%E9%87%87%E8%B4%AD%E5%BB%BA%E8%AE%AE/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E6%9F%A5%E8%AF%A2%E5%B7%B2%E5%94%AE%E9%87%87%E8%B4%AD%E5%BB%BA%E8%AE%AE/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E8%BF%9B%E5%BA%A6%E8%8E%B7%E5%8F%96/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E9%A2%84%E7%BA%A6%E5%85%A5%E5%BA%93%E5%8D%95%E6%96%B0%E5%A2%9E/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E9%A2%84%E7%BA%A6%E5%85%A5%E5%BA%93%E5%8D%95%E4%BF%AE%E6%94%B9/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E9%A2%84%E7%BA%A6%E5%85%A5%E5%BA%93%E5%8D%95%E5%8F%8D%E5%AE%A1%E6%A0%B8/",
    # 归档相关
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E5%BD%92%E6%A1%A3%E9%87%87%E8%B4%AD%E5%8D%95%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E5%BD%92%E6%A1%A3%E9%87%87%E8%B4%AD%E5%8D%95%E8%AF%A6%E6%83%85/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E5%BD%92%E6%A1%A3%E6%94%B6%E8%B4%A7%E5%8D%95%E6%9F%A5%E8%AF%A2%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E5%BD%92%E6%A1%A3%E6%94%B6%E8%B4%A7%E5%8D%95%E8%AF%A6%E6%83%85/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E5%BD%92%E6%A1%A3%E9%87%87%E9%80%80%E5%8D%95%E6%9F%A5%E8%AF%A2%E5%88%97%E8%A1%A8/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E5%BD%92%E6%A1%A3%E9%87%87%E9%80%80%E5%8D%95%E8%AF%A6%E6%83%85/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E5%BD%92%E6%A1%A3%E4%B8%8A%E6%9E%B6%E5%8D%95%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E9%87%87%E8%B4%AD/%E5%BD%92%E6%A1%A3%E4%B8%8A%E6%9E%B6%E5%8D%95%E8%AF%A6%E6%83%85%E6%9F%A5%E8%AF%A2/",
    # === API文档 - 快麦通 ===
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%BF%AB%E9%BA%A6%E9%80%9A/%E5%BF%AB%E9%BA%A6%E9%80%9A%E7%99%BB%E5%BD%95/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%BF%AB%E9%BA%A6%E9%80%9A/%E6%B3%A8%E5%86%8C%E5%88%86%E9%94%80%E5%95%86/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%BF%AB%E9%BA%A6%E9%80%9A/%E5%88%86%E9%94%80%E5%95%86%E4%BF%A1%E6%81%AF%E6%9F%A5%E8%AF%A2/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%BF%AB%E9%BA%A6%E9%80%9A/%E5%A2%9E%E5%8A%A0%E5%88%86%E9%94%80%E4%BD%99%E9%A2%9D/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%BF%AB%E9%BA%A6%E9%80%9A/%E6%B7%BB%E5%8A%A0%E5%88%86%E9%94%80%E5%95%86%E5%93%81/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%BF%AB%E9%BA%A6%E9%80%9A/%E5%88%86%E9%A1%B5%E6%9F%A5%E8%AF%A2%E4%BE%9B%E9%94%80%E5%B0%8F%E5%BA%97%E5%95%86%E5%93%81/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%BF%AB%E9%BA%A6%E9%80%9A/%E6%9F%A5%E8%AF%A2%E4%BE%9B%E9%94%80%E5%B0%8F%E5%BA%97%E5%95%86%E5%93%81%E8%AF%A6%E6%83%85/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%BF%AB%E9%BA%A6%E9%80%9A/%E6%8F%90%E4%BA%A4%E5%88%86%E9%94%80%E5%B0%8F%E5%BA%97%E5%95%86%E5%93%81%E7%9A%84%E5%90%8C%E6%AD%A5/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%BF%AB%E9%BA%A6%E9%80%9A/%E8%8E%B7%E5%8F%96%E5%B0%8F%E5%BA%97%E5%95%86%E5%93%81%E7%9A%84%E5%90%8C%E6%AD%A5%E7%8A%B6%E6%80%81/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%BF%AB%E9%BA%A6%E9%80%9A/%E6%9F%A5%E8%AF%A2%E5%9C%A8%E7%BA%BF%E6%94%AF%E4%BB%98%E6%96%B9%E5%BC%8F%E6%8F%90%E7%A4%BA%E6%96%87%E6%A1%88/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%BF%AB%E9%BA%A6%E9%80%9A/%E8%8E%B7%E5%8F%96%E6%9C%80%E6%96%B0%E7%9A%84%E8%A7%86%E9%A2%91%E9%93%BE%E6%8E%A5%E4%BF%A1%E6%81%AF/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%BF%AB%E9%BA%A6%E9%80%9A/%E4%BE%9B%E9%94%80%E5%95%86%E8%A7%86%E8%A7%86%E8%A7%82-%E5%88%86%E9%A1%B5%E4%BE%9B%E9%94%80%E5%B0%8F%E5%BA%97%E5%95%86%E5%93%81/",
    "https://open.kuaimai.com/docs/api/API%E6%96%87%E6%A1%A3/%E5%BF%AB%E9%BA%A6%E9%80%9A/%E4%BE%9B%E9%94%80%E5%95%86%E8%A7%86%E8%A7%86%E8%A7%82-%E6%9F%A5%E8%AF%A2%E4%BE%9B%E9%94%80%E5%B0%8F%E5%BA%97%E5%95%86%E5%93%81%E8%AF%A6%E6%83%85/",
    # === API场景说明 ===
    "https://open.kuaimai.com/docs/api/API%E5%9C%BA%E6%99%AF%E8%AF%B4%E6%98%8E/%E8%87%AA%E5%BB%BA%E5%B9%B3%E5%8F%B0/%E8%AE%A2%E5%8D%95%E4%B8%9A%E5%8A%A1%E5%AF%B9%E6%8E%A5/",
    "https://open.kuaimai.com/docs/api/API%E5%9C%BA%E6%99%AF%E8%AF%B4%E6%98%8E/%E8%87%AA%E5%BB%BA%E5%B9%B3%E5%8F%B0/%E5%95%86%E5%93%81%E4%B8%9A%E5%8A%A1%E5%AF%B9%E6%8E%A5/",
    "https://open.kuaimai.com/docs/api/API%E5%9C%BA%E6%99%AF%E8%AF%B4%E6%98%8E/%E8%87%AA%E5%BB%BA%E5%B9%B3%E5%8F%B0/%E5%BA%93%E5%AD%98%E4%B8%9A%E5%8A%A1%E5%AF%B9%E6%8E%A5/",
    "https://open.kuaimai.com/docs/api/API%E5%9C%BA%E6%99%AF%E8%AF%B4%E6%98%8E/%E8%87%AA%E5%BB%BA%E5%B9%B3%E5%8F%B0/%E5%94%AE%E5%90%8E%E4%B8%9A%E5%8A%A1%E5%AF%B9%E6%8E%A5/",
    "https://open.kuaimai.com/docs/api/API%E5%9C%BA%E6%99%AF%E8%AF%B4%E6%98%8E/%E8%87%AA%E5%BB%BA%E5%B9%B3%E5%8F%B0/%E5%BA%93%E5%AD%98%E4%B8%8A%E4%BC%A0%E4%B8%BB%E5%8A%A8%E9%80%9A%E7%9F%A5/",
    "https://open.kuaimai.com/docs/api/API%E5%9C%BA%E6%99%AF%E8%AF%B4%E6%98%8E/%E8%87%AA%E5%BB%BA%E5%B9%B3%E5%8F%B0/%E8%AE%A2%E5%8D%95%E5%8F%91%E8%B4%A7%E4%B8%BB%E5%8A%A8%E9%80%9A%E7%9F%A5/",
]


def extract_page_content(html: str, url: str) -> str:
    """从HTML中提取页面的完整文本内容"""
    soup = BeautifulSoup(html, "html.parser")

    # 获取页面标题
    title_tag = soup.find("title")
    title = title_tag.get_text().replace(" | 快麦开放平台", "") if title_tag else "未知"

    # 找到article标签（Docusaurus主内容区）
    article = soup.find("article")
    if not article:
        return f"# {title}\n\n[页面内容无法提取]\n"

    result_parts = []
    result_parts.append(f"# {title}")

    # 递归处理所有元素
    def process_element(elem, depth=0):
        if elem.name is None:  # 文本节点
            text = elem.strip()
            if text:
                return text
            return ""

        if elem.name in ["script", "style", "svg", "button"]:
            return ""

        if elem.name == "h1":
            return f"\n## {elem.get_text(strip=True)}\n"
        elif elem.name == "h2":
            return f"\n### {elem.get_text(strip=True)}\n"
        elif elem.name == "h3":
            return f"\n#### {elem.get_text(strip=True)}\n"
        elif elem.name == "h4":
            return f"\n##### {elem.get_text(strip=True)}\n"
        elif elem.name == "table":
            return process_table(elem)
        elif elem.name == "pre":
            # 代码块
            code = elem.get_text()
            return f"\n```json\n{code}\n```\n"
        elif elem.name == "code" and elem.parent.name != "pre":
            return f"`{elem.get_text()}`"
        elif elem.name in ["ul", "ol"]:
            items = []
            for li in elem.find_all("li", recursive=False):
                items.append(f"- {li.get_text(strip=True)}")
            return "\n".join(items) + "\n"
        elif elem.name == "p":
            return f"\n{elem.get_text(strip=True)}\n"
        elif elem.name == "a":
            href = elem.get("href", "")
            text = elem.get_text(strip=True)
            if href and text:
                return f"[{text}]({href})"
            return text
        elif elem.name in ["div", "section", "main", "article", "header", "footer", "span", "strong", "em", "b", "i"]:
            parts = []
            for child in elem.children:
                if hasattr(child, "name"):
                    part = process_element(child, depth + 1)
                else:
                    text = str(child).strip()
                    if text:
                        part = text
                    else:
                        part = ""
                if part:
                    parts.append(part)
            return " ".join(parts) if elem.name == "span" else "\n".join(parts)
        else:
            # 其他元素，提取文本
            parts = []
            for child in elem.children:
                if hasattr(child, "name"):
                    part = process_element(child, depth + 1)
                else:
                    text = str(child).strip()
                    if text:
                        part = text
                    else:
                        part = ""
                if part:
                    parts.append(part)
            return "\n".join(parts)

    def process_table(table):
        """处理表格，转换为Markdown格式"""
        rows = []
        thead = table.find("thead")
        tbody = table.find("tbody")

        if thead:
            header_row = thead.find("tr")
            if header_row:
                headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]
                rows.append("| " + " | ".join(headers) + " |")
                rows.append("| " + " | ".join(["---"] * len(headers)) + " |")

        if tbody:
            for tr in tbody.find_all("tr"):
                cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if cells:
                    rows.append("| " + " | ".join(cells) + " |")
        elif not thead:
            # 没有thead和tbody，直接处理tr
            for i, tr in enumerate(table.find_all("tr")):
                cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if cells:
                    rows.append("| " + " | ".join(cells) + " |")
                    if i == 0:
                        rows.append("| " + " | ".join(["---"] * len(cells)) + " |")

        return "\n" + "\n".join(rows) + "\n" if rows else ""

    content = process_element(article)
    result_parts.append(content)

    # 清理多余空行
    result = "\n".join(result_parts)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def crawl_all_pages():
    """爬取所有API文档页面"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })

    all_content = []
    failed = []
    total = len(API_DOC_URLS)

    for i, url in enumerate(API_DOC_URLS):
        decoded_url = unquote(url)
        # 从URL提取页面名称
        parts = decoded_url.replace("https://open.kuaimai.com/docs/api/", "").strip("/").split("/")
        page_name = " > ".join(parts)

        print(f"[{i+1}/{total}] 正在爬取: {page_name}")

        try:
            resp = session.get(url, timeout=30, allow_redirects=True)
            resp.encoding = "utf-8"

            if resp.status_code == 200:
                content = extract_page_content(resp.text, url)
                separator = f"\n\n{'='*80}\n"
                all_content.append(f"{separator}=== {page_name} ===\n{'='*80}\n\n{content}")
                print(f"  ✓ 成功提取内容 ({len(content)} 字符)")
            else:
                failed.append((page_name, f"HTTP {resp.status_code}"))
                print(f"  ✗ HTTP {resp.status_code}")

            # 限速，避免过于频繁
            time.sleep(0.3)

        except Exception as e:
            failed.append((page_name, str(e)))
            print(f"  ✗ 错误: {e}")

    # 写入文件
    output_path = Path("/Users/wucong/EVERYDAYAIONE/docs/document/TECH_快麦API文档_完整.md")
    header = f"""# 快麦开放平台 API 完整文档

> 爬取时间：{time.strftime('%Y-%m-%d %H:%M:%S')}
> 来源：https://open.kuaimai.com/docs/api/
> 总页面数：{total}，成功：{total - len(failed)}，失败：{len(failed)}
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("\n".join(all_content))

    print(f"\n{'='*50}")
    print(f"完成！共爬取 {total} 个页面")
    print(f"成功: {total - len(failed)}")
    print(f"失败: {len(failed)}")
    if failed:
        print("\n失败列表:")
        for name, reason in failed:
            print(f"  - {name}: {reason}")
    print(f"\n输出文件: {output_path}")


if __name__ == "__main__":
    crawl_all_pages()
