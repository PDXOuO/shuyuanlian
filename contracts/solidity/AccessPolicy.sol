// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

interface IRegistry {
    function ownerOf(bytes32 id) external view returns (address);
    function statusOf(bytes32 id) external view returns (uint8);
    function priceOf(bytes32 id) external view returns (uint256);
}

interface ISettlement {
    function settle(bytes32 elementId, uint256 amountMicro, bytes32 policyId, address payer) external;
}

/**
 * @title AccessPolicy
 * @notice 授权策略状态机：谁、为什么目的、能用几次、到什么时候。
 *         状态：0 ACTIVE / 1 EXHAUSTED / 2 EXPIRED / 3 REVOKED / 4 FROZEN
 */
contract AccessPolicy {
    uint8 public constant ACTIVE = 0;
    uint8 public constant EXHAUSTED = 1;
    uint8 public constant EXPIRED = 2;
    uint8 public constant REVOKED = 3;
    uint8 public constant FROZEN = 4;

    struct Policy {
        bytes32 id;
        bytes32 elementId;
        address owner;
        address consumer;
        string purpose;
        uint32 maxCalls;
        uint32 used;
        uint256 priceMicro;
        uint64 createdAt;
        uint64 expireAt;
        bool revoked;
        bool frozen;
    }

    IRegistry public registry;
    ISettlement public settlement;
    address public governance;
    address public traceContract;

    mapping(bytes32 => Policy) private _policies;
    mapping(bytes32 => bytes32[]) private _byElement;
    uint256 public policyCount;

    event AccessGranted(bytes32 indexed id, bytes32 indexed elementId, address consumer, uint32 maxCalls);
    event AccessConsumed(bytes32 indexed id, uint32 seq, uint32 remaining);
    event AccessRevoked(bytes32 indexed id, string reason);
    event PoliciesFrozen(bytes32 indexed elementId, uint256 count, bytes32 caseId);

    constructor(address registryAddr, address settlementAddr) {
        registry = IRegistry(registryAddr);
        settlement = ISettlement(settlementAddr);
        governance = msg.sender;
    }

    modifier onlyGovernance() {
        require(msg.sender == governance, "E_AUTH: governance only");
        _;
    }

    function setTraceContract(address who) external onlyGovernance {
        traceContract = who;
    }

    function grant(
        bytes32 elementId,
        address consumer,
        string calldata purpose,
        uint32 maxCalls,
        uint64 ttlSeconds,
        uint256 priceMicro
    ) external returns (bytes32 policyId) {
        require(registry.statusOf(elementId) == 0, "E_FROZEN: element frozen");
        address owner = registry.ownerOf(elementId);
        require(msg.sender == owner, "E_OWNER: only holder");
        require(consumer != owner, "E_SELF: self grant");
        require(bytes(purpose).length > 0, "E_PURPOSE: empty");
        require(maxCalls >= 1, "E_CALLS: min 1");
        require(ttlSeconds >= 1, "E_TTL: min 1");

        policyId = keccak256(abi.encodePacked(elementId, consumer, purpose, block.timestamp, maxCalls));
        require(_policies[policyId].id == bytes32(0), "E_DUP: policy exists");

        Policy storage p = _policies[policyId];
        p.id = policyId;
        p.elementId = elementId;
        p.owner = owner;
        p.consumer = consumer;
        p.purpose = purpose;
        p.maxCalls = maxCalls;
        p.priceMicro = priceMicro == 0 ? registry.priceOf(elementId) : priceMicro;
        p.createdAt = uint64(block.timestamp);
        p.expireAt = uint64(block.timestamp + ttlSeconds);
        _byElement[elementId].push(policyId);
        policyCount += 1;
        emit AccessGranted(policyId, elementId, consumer, maxCalls);
    }

    function statusOf(bytes32 policyId) public view returns (uint8) {
        Policy storage p = _policies[policyId];
        require(p.id != bytes32(0), "E_NOT_FOUND");
        if (p.revoked) return REVOKED;
        if (p.frozen) return FROZEN;
        if (p.used >= p.maxCalls) return EXHAUSTED;
        if (block.timestamp >= p.expireAt) return EXPIRED;
        return ACTIVE;
    }

    function remaining(bytes32 policyId) external view returns (uint32) {
        Policy storage p = _policies[policyId];
        return p.used >= p.maxCalls ? 0 : p.maxCalls - p.used;
    }

    /// @notice 受控消费：身份 → 状态 → 用途，顺序刻意如此，避免泄露策略存在性
    function consume(bytes32 policyId, string calldata purpose) external returns (uint32 seq) {
        Policy storage p = _policies[policyId];
        require(p.id != bytes32(0), "E_NOT_FOUND");
        require(msg.sender == p.consumer, "E_CONSUMER: not grantee");
        uint8 st = statusOf(policyId);
        if (st == EXHAUSTED) revert("E_EXHAUSTED: quota used up");
        if (st == EXPIRED) revert("E_EXPIRED: out of date");
        if (st == REVOKED) revert("E_REVOKED: revoked");
        if (st == FROZEN) revert("E_FROZEN: frozen");
        require(
            bytes(purpose).length == 0 || keccak256(bytes(purpose)) == keccak256(bytes(p.purpose)),
            "E_PURPOSE_MISMATCH: not in whitelist"
        );

        p.used += 1;
        seq = p.used;
        if (p.priceMicro > 0) {
            settlement.settle(p.elementId, p.priceMicro, policyId, p.consumer);
        }
        emit AccessConsumed(policyId, seq, p.maxCalls - seq);
    }

    function revoke(bytes32 policyId, string calldata reason) external {
        Policy storage p = _policies[policyId];
        require(p.id != bytes32(0), "E_NOT_FOUND");
        require(msg.sender == p.owner || msg.sender == p.consumer || msg.sender == governance, "E_AUTH: revoke");
        p.revoked = true;
        emit AccessRevoked(policyId, reason);
    }

    /// @notice 泄漏命中时由追溯合约调用，冻结该元件全部在效授权
    function freezeByLeak(bytes32 elementId, bytes32 caseId) external returns (uint256 n) {
        require(msg.sender == traceContract, "E_AUTH: trace only");
        bytes32[] storage ids = _byElement[elementId];
        for (uint256 i = 0; i < ids.length; i++) {
            Policy storage p = _policies[ids[i]];
            if (!p.frozen && !p.revoked) {
                p.frozen = true;
                n += 1;
            }
        }
        emit PoliciesFrozen(elementId, n, caseId);
    }
}
