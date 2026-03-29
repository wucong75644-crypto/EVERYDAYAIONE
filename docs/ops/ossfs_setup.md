# ossfs 挂载配置指南

## 作用
将阿里云 OSS Bucket 挂载为 ECS 本地目录，AI 可直接读写文件（内网零流量费）。

## 1. 安装 ossfs

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y gdebi-core
wget https://gosspublic.alicdn.com/ossfs/ossfs_1.91.3_ubuntu22.04_amd64.deb
sudo gdebi ossfs_1.91.3_ubuntu22.04_amd64.deb

# CentOS/RHEL
sudo yum install -y fuse
wget https://gosspublic.alicdn.com/ossfs/ossfs_1.91.3_centos7.0_x86_64.rpm
sudo rpm -ivh ossfs_1.91.3_centos7.0_x86_64.rpm
```

> 最新版本见: https://help.aliyun.com/zh/oss/developer-reference/install-ossfs

## 2. 配��凭证

```bash
# 格式: BucketName:AccessKeyId:AccessKeySecret
echo "你的BucketName:你的AccessKeyId:你的AccessKeySecret" > /etc/passwd-ossfs
chmod 600 /etc/passwd-ossfs
```

## 3. 创建挂载目录

```bash
sudo mkdir -p /mnt/oss-workspace
sudo chown $(whoami):$(whoami) /mnt/oss-workspace
```

## 4. 手动挂载测试

```bash
# 内网端点挂载（同地域 ECS，零流量费）
# -o url= 填你的内网端点（如 oss-cn-hangzhou-internal.aliyuncs.com）
ossfs 你的BucketName /mnt/oss-workspace \
  -o url=http://oss-cn-hangzhou-internal.aliyuncs.com \
  -o allow_other \
  -o umask=022 \
  -o max_stat_cache_size=10000 \
  -o multipart_size=128 \
  -o parallel_count=5 \
  -o dbglevel=warn

# 验证
echo "hello ossfs" > /mnt/oss-workspace/test.txt
cat /mnt/oss-workspace/test.txt
rm /mnt/oss-workspace/test.txt
```

## 5. 开机自动挂载

```bash
# 写入 /etc/fstab
echo "你的BucketName /mnt/oss-workspace fuse.ossfs _netdev,url=http://oss-cn-hangzhou-internal.aliyuncs.com,allow_other,umask=022,max_stat_cache_size=10000 0 0" | sudo tee -a /etc/fstab

# 验证 fstab
sudo mount -a
df -h | grep oss
```

## 6. 环境变量���置

在 `.env` 中添加:

```bash
# ossfs 挂载路径（AI 文件操作的根目录）
FILE_WORKSPACE_ROOT=/mnt/oss-workspace/workspace
```

## 目录结构

```
/mnt/oss-workspace/          ← ossfs 挂载点（= OSS Bucket 根）
  └── workspace/             ← AI 文件操作根目录
      ├── org/{org_id}/      ← 企业用户文件
      │   └── {user_id}/
      │       ├── uploads/   ← 用户上传���文件
      │       └── outputs/   ← AI 生成的文件
      └── personal/{user_hash}/  ← 散客文件
          ├── uploads/
          └── outputs/
```

## 注意事项

- ossfs **必须用内网端点**（同地域 ECS），否则会产生外网流量费
- ossfs 不适合高频随机写入，文件分析（顺序读）场景完全够用
- CDN URL 用于前端下载/预览: `https://{cdn_domain}/workspace/org/{org_id}/{user_id}/uploads/file.csv`
