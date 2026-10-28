# 数元链 · FISCO BCOS 部署说明

本目录提供把数元链的 Solidity 合约部署到 FISCO BCOS 3.x / 4.x 联盟链的操作步骤。
默认演示路径使用 Python 内置联盟链引擎（零依赖、可离线复现），本目录是**生产部署路径**。

## 1. 前置条件

- Docker 20.10+ 与 docker-compose（或用官方 `build_chain.sh` 直接在 Linux 主机建链）
- FISCO BCOS 控制台（console）3.x 或 4.x
- Python 3.10+（仅用于把交易脚本转成控制台命令，可选）

## 2. 建链（4 节点，PBFT）

```bash
# 下载建链脚本（官方）
curl -#LO https://github.com/FISCO-BCOS/FISCO-BCOS/releases/download/v3.7.1/build_chain.sh
chmod +x build_chain.sh

# 4 个节点、单群组，输出到 nodes/
./build_chain.sh -l 127.0.0.1:4 -p 30300,20200,8545 -o nodes

# 启动全部节点
bash nodes/127.0.0.1/start_all.sh
```

> 生产环境应把 4 个节点分散到不同机构、不同机房，并由各机构独立保管 `node.key`。

## 3. 部署合约

合约源码位于 `../solidity/`，编译产物（ABI + bytecode）可用
`python contracts/compile_check.py` 生成到 `contracts/build/`。

在 FISCO BCOS 控制台中按顺序部署（后两者依赖前者的地址）：

```solidity
// 1) 元件注册表
deploy DataElementRegistry

// 2) 结算（构造参数 = 注册表地址）
deploy Settlement <registryAddress>

// 3) 授权策略（构造参数 = 注册表地址、结算地址）
deploy AccessPolicy <registryAddress> <settlementAddress>

// 4) 溯源冻结（构造参数 = 注册表地址、授权合约地址）
deploy TraceAndFreeze <registryAddress> <accessAddress>

// 5) 聚合锚定
deploy AggregateAnchor
```

部署完成后做三处权限绑定（治理方私钥执行）：

```solidity
// 结算合约只认授权合约
settlement.setAccessContract(<accessAddress>)

// 授权合约与注册表认识溯源合约
accessPolicy.setTraceContract(<traceAddress>)
registry.setTraceContract(<traceAddress>)

// 把溯源机构地址设为预言机
traceAndFreeze.setOracle(<oracleAddress>, true)
```

## 4. 验证

```solidity
// 登记一个元件（参数为结构体）
registry.registerElement(("0x1111...", "测试元件", "0xaaaa...", "0xbbbb...", [], [], 10000000, 6000, 432))

// 授权
accessPolicy.grant("0x1111...", <bankAddress>, "风控建模", 100, 604800, 20000000)

// 消费（触发自动分账）
accessPolicy.consume(<policyId>, "风控建模")

// 查看余额
settlement.balance(<ownerAddress>)
```

## 5. 与 Python 参考实现的差异

| 项目 | Python 内置引擎 | FISCO BCOS |
|---|---|---|
| 元件 ID | 字符串（如 `EL-PARK-ELEC`） | `bytes32`（字符串需 keccak256 或左填充） |
| 金额单位 | 微元整数 | 同（`uint256` 微元） |
| 授权用途 | 字符串白名单数组 | 单字符串 + keccak256 比较 |
| 时间 | 交易时间戳 | `block.timestamp` |
| 指纹码字 | `code_hex` 字符串 | `bytes32` |

业务语义、错误码、状态机完全一致，便于对照阅读与迁移。

## 6. 注意事项

1. 本目录脚本未在参赛环境实测（提交环境无 Docker），仅作部署参考；
2. 生产环境务必更换 `build_chain.sh` 生成的默认证书，并启用国密选项（`-g`）；
3. `Settlement` 中的跨合约调用使用 `staticcall` + `abi.decode`，若升级注册表接口需同步修改；
4. 建议为治理操作启用多签（如 Gnosis Safe 或 FISCO BCOS 自带的权限治理）。
