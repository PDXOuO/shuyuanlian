// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * @title AggregateAnchor
 * @notice 隐私聚合结果的链上锚定：只记录参与方、参数与结果哈希，
 *         任何明文（个体值或聚合值）都不写入账本。
 */
contract AggregateAnchor {
    struct Round {
        bytes32 id;
        bytes32 resultHash;
        uint16 partyCount;
        uint16 epsilonBp;      // ε × 100，便于整数存储
        string scheme;
        address anchoredBy;
        uint64 ts;
    }

    address public governance;
    mapping(bytes32 => Round) private _rounds;
    bytes32[] public roundIds;

    event AggregateAnchored(bytes32 indexed roundId, uint16 partyCount, uint16 epsilonBp, bytes32 resultHash);

    constructor() {
        governance = msg.sender;
    }

    function anchor(
        bytes32 roundId,
        bytes32 resultHash,
        uint16 partyCount,
        uint16 epsilonBp,
        string calldata scheme
    ) external {
        require(roundId != bytes32(0), "E_ROUND: empty");
        require(resultHash != bytes32(0), "E_HASH: empty");
        require(partyCount >= 2, "E_PARTIES: min 2");
        require(_rounds[roundId].id == bytes32(0), "E_DUP: round anchored");

        _rounds[roundId] = Round(
            roundId, resultHash, partyCount, epsilonBp, scheme, msg.sender, uint64(block.timestamp)
        );
        roundIds.push(roundId);
        emit AggregateAnchored(roundId, partyCount, epsilonBp, resultHash);
    }

    function roundOf(bytes32 roundId)
        external
        view
        returns (bytes32 resultHash, uint16 partyCount, uint16 epsilonBp, address anchoredBy, uint64 ts)
    {
        Round storage r = _rounds[roundId];
        require(r.id != bytes32(0), "E_NOT_FOUND: round");
        return (r.resultHash, r.partyCount, r.epsilonBp, r.anchoredBy, r.ts);
    }

    function roundCount() external view returns (uint256) {
        return roundIds.length;
    }
}
