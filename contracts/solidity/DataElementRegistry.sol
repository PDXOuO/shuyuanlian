// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * @title DataElementRegistry
 * @notice 数据元件确权与血缘登记。原始数据不上链，只保存内容哈希、字段模式哈希与血缘父节点。
 *         与 syuan/contracts/registry.py 接口同构。
 */
contract DataElementRegistry {
    uint8 public constant STATUS_ACTIVE = 0;
    uint8 public constant STATUS_FROZEN = 1;
    uint8 public constant MAX_DEPTH = 8;

    struct Parent {
        bytes32 id;
        uint32 weight;
    }

    /// @dev 参数打包为结构体，规避 EVM 栈深限制（stack too deep）
    struct NewElement {
        bytes32 id;
        string name;
        bytes32 contentHash;
        bytes32 schemaHash;
        bytes32[] parentIds;
        uint32[] weights;
        uint256 priceMicro;
        uint16 selfShareBp;
        uint32 rows;
    }

    struct Element {
        bytes32 id;
        string name;
        address owner;
        bytes32 contentHash;
        bytes32 schemaHash;
        Parent[] parents;
        uint256 priceMicro;
        uint16 selfShareBp;
        uint32 rows;
        uint8 depth;
        uint8 status;
        uint64 createdAt;
    }

    address public governance;
    address public traceContract;
    uint256 public elementCount;

    mapping(bytes32 => Element) private _elements;
    mapping(bytes32 => bool) private _exists;
    mapping(address => bool) public isMember;

    event ElementRegistered(bytes32 indexed id, string name, address indexed owner, uint8 depth);
    event PriceUpdated(bytes32 indexed id, uint256 priceMicro);
    event ElementFrozen(bytes32 indexed id, string reason);

    modifier onlyGovernance() {
        require(msg.sender == governance, "E_AUTH: governance only");
        _;
    }

    constructor() {
        governance = msg.sender;
        isMember[msg.sender] = true;
    }

    function setMember(address who, bool ok) external onlyGovernance {
        isMember[who] = ok;
    }

    function setTraceContract(address who) external onlyGovernance {
        traceContract = who;
    }

    /// @notice 登记元件；父节点必须已存在、未冻结，权重为正，深度不超过 MAX_DEPTH
    function registerElement(NewElement calldata e) external returns (uint8 depth) {
        require(!_exists[e.id], "E_DUP: element exists");
        require(e.contentHash != bytes32(0) && e.schemaHash != bytes32(0), "E_HASH: empty hash");
        require(e.selfShareBp <= 10_000, "E_SHARE: out of range");
        require(e.parentIds.length == e.weights.length, "E_ARGS: length mismatch");

        for (uint256 i = 0; i < e.parentIds.length; i++) {
            require(_exists[e.parentIds[i]], "E_PARENT: not found");
            require(_elements[e.parentIds[i]].status == STATUS_ACTIVE, "E_PARENT_FROZEN");
            require(e.weights[i] > 0, "E_WEIGHT: must be positive");
            uint8 pd = _elements[e.parentIds[i]].depth;
            require(pd + 1 <= MAX_DEPTH, "E_DEPTH: too deep");
            if (pd + 1 > depth) depth = pd + 1;
        }

        Element storage el = _elements[e.id];
        el.id = e.id;
        el.name = e.name;
        el.owner = msg.sender;
        el.contentHash = e.contentHash;
        el.schemaHash = e.schemaHash;
        el.priceMicro = e.priceMicro;
        el.selfShareBp = e.selfShareBp;
        el.rows = e.rows;
        el.depth = depth;
        el.status = STATUS_ACTIVE;
        el.createdAt = uint64(block.timestamp);
        for (uint256 i = 0; i < e.parentIds.length; i++) {
            el.parents.push(Parent(e.parentIds[i], e.weights[i]));
        }
        _exists[e.id] = true;
        elementCount += 1;
        emit ElementRegistered(e.id, e.name, msg.sender, depth);
    }

    function exists(bytes32 id) external view returns (bool) {
        return _exists[id];
    }

    function ownerOf(bytes32 id) external view returns (address) {
        require(_exists[id], "E_NOT_FOUND");
        return _elements[id].owner;
    }

    function statusOf(bytes32 id) external view returns (uint8) {
        require(_exists[id], "E_NOT_FOUND");
        return _elements[id].status;
    }

    function priceOf(bytes32 id) external view returns (uint256) {
        require(_exists[id], "E_NOT_FOUND");
        return _elements[id].priceMicro;
    }

    function selfShareOf(bytes32 id) external view returns (uint16) {
        require(_exists[id], "E_NOT_FOUND");
        return _elements[id].selfShareBp;
    }

    /// @notice 返回父节点 ID 与权重两个平行数组，便于结算合约直接解码
    function parentsOf(bytes32 id) external view returns (bytes32[] memory ids, uint32[] memory weights) {
        require(_exists[id], "E_NOT_FOUND");
        Parent[] storage ps = _elements[id].parents;
        ids = new bytes32[](ps.length);
        weights = new uint32[](ps.length);
        for (uint256 i = 0; i < ps.length; i++) {
            ids[i] = ps[i].id;
            weights[i] = ps[i].weight;
        }
    }

    function setPrice(bytes32 id, uint256 priceMicro) external {
        require(_exists[id], "E_NOT_FOUND");
        require(msg.sender == _elements[id].owner || msg.sender == governance, "E_OWNER");
        _elements[id].priceMicro = priceMicro;
        emit PriceUpdated(id, priceMicro);
    }

    /// @notice 冻结：仅追溯合约或治理方可调用
    function freezeElement(bytes32 id, string calldata reason) external {
        require(msg.sender == traceContract || msg.sender == governance, "E_AUTH: freeze");
        require(_exists[id], "E_NOT_FOUND");
        _elements[id].status = STATUS_FROZEN;
        emit ElementFrozen(id, reason);
    }
}
