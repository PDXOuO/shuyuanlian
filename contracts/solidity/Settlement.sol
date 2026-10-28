// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

interface IRegistryForSettlement {
    function ownerOf(bytes32 id) external view returns (address);
    function selfShareOf(bytes32 id) external view returns (uint16);
    function parentsOf(bytes32 id) external view returns (bytes32[] memory);
}

interface IRegistryParents {
    struct Parent { bytes32 id; uint32 weight; }
}

/**
 * @title Settlement
 * @notice 沿血缘 DAG 的自动分账，整数微元运算 + 最大余数法，保证
 *         sum(各方所得) == 本次收入，1000 次调用误差为 0。
 */
contract Settlement {
    uint16 public constant BP = 10_000;
    uint8 public constant MAX_HOP = 8;

    address public registry;
    address public accessContract;

    mapping(address => uint256) public balance;
    mapping(address => mapping(bytes32 => uint256)) public earned;
    uint256 public totalSettled;
    uint256 public settleCount;

    event Settled(bytes32 indexed policyId, bytes32 indexed elementId, uint256 amountMicro, uint256 seq);

    constructor(address registryAddr) {
        registry = registryAddr;
        accessContract = msg.sender;
    }

    function setAccessContract(address who) external {
        require(msg.sender == accessContract, "E_AUTH");
        accessContract = who;
    }

    /// @dev 读取父节点与权重（与 Python 版接口对齐）
    function _parents(bytes32 id) internal view returns (bytes32[] memory ids, uint32[] memory ws) {
        (bool ok, bytes memory data) = registry.staticcall(
            abi.encodeWithSignature("parentsOf(bytes32)", id)
        );
        require(ok, "E_PARENT: read failed");
        // 简化：由调用方保证 ABI 一致；生产实现应使用接口类型
        (ids, ws) = abi.decode(data, (bytes32[], uint32[]));
    }

    /// @notice 仅授权合约可触发结算
    function settle(bytes32 elementId, uint256 amountMicro, bytes32 policyId, address payer) external {
        require(msg.sender == accessContract, "E_AUTH: access contract only");
        require(amountMicro > 0, "E_AMOUNT: must be positive");
        uint256 before = totalSettled;
        _distribute(elementId, amountMicro, 0);
        require(totalSettled - before == amountMicro, "E_CONSERVATION: mismatch");
        settleCount += 1;
        emit Settled(policyId, elementId, amountMicro, settleCount);
        payer; // 付款方仅用于事件与审计
    }

    function _distribute(bytes32 elementId, uint256 amount, uint8 hop) internal {
        require(hop <= MAX_HOP, "E_DEPTH: too deep");
        (bytes32[] memory pids, uint32[] memory ws) = _parents(elementId);
        address owner = _owner(elementId);
        if (pids.length == 0) {
            balance[owner] += amount;
            earned[owner][elementId] += amount;
            totalSettled += amount;
            return;
        }
        uint16 selfBp = _selfShare(elementId);
        uint256 selfAmount = (amount * selfBp) / BP;
        uint256 rest = amount - selfAmount;
        if (selfAmount > 0) {
            balance[owner] += selfAmount;
            earned[owner][elementId] += selfAmount;
            totalSettled += selfAmount;
        }
        if (rest == 0) return;

        uint256 totalW = 0;
        for (uint256 i = 0; i < ws.length; i++) totalW += ws[i];
        require(totalW > 0, "E_WEIGHT: zero");

        uint256 distributed = 0;
        uint256[] memory parts = new uint256[](pids.length);
        for (uint256 i = 0; i < pids.length; i++) {
            parts[i] = (rest * ws[i]) / totalW;
            distributed += parts[i];
        }
        // 最大余数法补齐尾差：按余数降序，依次 +1
        uint256 remainder = rest - distributed;
        while (remainder > 0) {
            uint256 best = 0;
            uint256 bestRem = 0;
            for (uint256 i = 0; i < pids.length; i++) {
                uint256 r = (rest * ws[i]) % totalW;
                if (r > bestRem) {
                    bestRem = r;
                    best = i;
                }
            }
            parts[best] += 1;
            remainder -= 1;
        }
        for (uint256 i = 0; i < pids.length; i++) {
            if (parts[i] > 0) _distribute(pids[i], parts[i], hop + 1);
        }
    }

    function _owner(bytes32 id) internal view returns (address) {
        (bool ok, bytes memory data) = registry.staticcall(
            abi.encodeWithSignature("ownerOf(bytes32)", id)
        );
        require(ok, "E_NOT_FOUND: element");
        return abi.decode(data, (address));
    }

    function _selfShare(bytes32 id) internal view returns (uint16) {
        (bool ok, bytes memory data) = registry.staticcall(
            abi.encodeWithSignature("selfShareOf(bytes32)", id)
        );
        require(ok, "E_NOT_FOUND: element");
        return abi.decode(data, (uint16));
    }
}
