// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

interface IFreezeTarget {
    function freezeElement(bytes32 id, string calldata reason) external;
    function freezeByLeak(bytes32 elementId, bytes32 caseId) external returns (uint256);
    function ownerOf(bytes32 id) external view returns (address);
}

/**
 * @title TraceAndFreeze
 * @notice 指纹登记、泄漏取证与自动冻结。链上只存码字与证据哈希，
 *         检测算法在链下执行，结论由具备 ROLE_ORACLE 的机构提交。
 */
contract TraceAndFreeze {
    uint16 public constant HIT_THRESHOLD_BP = 8_500;

    struct Fingerprint {
        bytes32 code;
        uint16 bits;
        uint32 rows;
        uint64 registeredAt;
        address registeredBy;
        bool exists;
    }

    struct Case {
        bytes32 id;
        bytes32 elementId;
        uint16 similarityBp;
        bytes32 evidenceHash;
        address reporter;
        uint64 createdAt;
        uint256 frozenPolicies;
    }

    IFreezeTarget public registry;
    IFreezeTarget public access;
    address public governance;
    mapping(address => bool) public isOracle;
    mapping(bytes32 => Fingerprint) private _fp;
    mapping(bytes32 => Case) private _caseByElement;
    mapping(bytes32 => bytes32) private _caseElement;
    bytes32[] public caseIds;

    event FingerprintRegistered(bytes32 indexed elementId, bytes32 code, uint16 bits);
    event LeakDetected(bytes32 indexed caseId, bytes32 indexed elementId, uint16 similarityBp, uint256 frozenPolicies);

    constructor(address registryAddr, address accessAddr) {
        registry = IFreezeTarget(registryAddr);
        access = IFreezeTarget(accessAddr);
        governance = msg.sender;
        isOracle[msg.sender] = true;
    }

    function setOracle(address who, bool ok) external {
        require(msg.sender == governance, "E_AUTH: governance only");
        isOracle[who] = ok;
    }

    function registerFingerprint(bytes32 elementId, bytes32 code, uint16 bits, uint32 rows) external {
        require(registry.ownerOf(elementId) == msg.sender, "E_OWNER: holder only");
        require(code != bytes32(0), "E_CODE: empty");
        require(bits >= 32, "E_CODE: too short");
        _fp[elementId] = Fingerprint(code, bits, rows, uint64(block.timestamp), msg.sender, true);
        emit FingerprintRegistered(elementId, code, bits);
    }

    function fingerprintOf(bytes32 elementId) external view returns (bytes32 code, uint16 bits, uint32 rows) {
        Fingerprint storage f = _fp[elementId];
        require(f.exists, "E_NO_FP");
        return (f.code, f.bits, f.rows);
    }

    /// @notice 命中上报：自动冻结元件与其全部在效授权
    function reportLeak(
        bytes32 elementId,
        uint16 similarityBp,
        bytes32 evidenceHash,
        string calldata sampleSource
    ) external returns (bytes32 caseId) {
        require(isOracle[msg.sender], "E_ROLE: oracle only");
        require(_fp[elementId].exists, "E_NO_FP: no fingerprint");
        require(similarityBp >= HIT_THRESHOLD_BP, "E_LOW_CONF: below threshold");
        require(evidenceHash != bytes32(0), "E_EVIDENCE: empty");
        require(_caseByElement[elementId].id == bytes32(0), "E_DUP_CASE: exists");

        caseId = keccak256(abi.encodePacked(elementId, evidenceHash, block.timestamp));
        uint256 frozen = access.freezeByLeak(elementId, caseId);
        registry.freezeElement(elementId, "leak detected");

        _caseByElement[elementId] = Case(
            caseId, elementId, similarityBp, evidenceHash, msg.sender, uint64(block.timestamp), frozen
        );
        _caseElement[caseId] = elementId;
        caseIds.push(caseId);
        sampleSource; // 仅用于链下索引
        emit LeakDetected(caseId, elementId, similarityBp, frozen);
    }

    function caseOf(bytes32 caseId)
        external
        view
        returns (bytes32 elementId, uint16 similarityBp, bytes32 evidenceHash, uint256 frozenPolicies)
    {
        bytes32 eid = _caseElement[caseId];
        require(eid != bytes32(0), "E_NOT_FOUND: case");
        Case storage c = _caseByElement[eid];
        return (c.elementId, c.similarityBp, c.evidenceHash, c.frozenPolicies);
    }

    function caseCount() external view returns (uint256) {
        return caseIds.length;
    }
}
