package modular_seed;

import battlecode.common.*;

import java.util.ArrayList;
import java.util.Random;

/**
 * Entry point + shared state + turn dispatch (glue, not a mutable component).
 *
 * Behavioral clone of bots/original_seed/lectureplayer/RobotPlayer.java with the
 * logic split into the five components: Economy, Combat, Defense, Navigation,
 * Strategy. Same logic, same order of operations, same RNG usage sequence.
 */
public class RobotPlayer {
    public static enum State {
        INITIALIZE,
        FIND_CHEESE,
        RETURN_TO_KING,
        BUILD_TRAPS,
        EXPLORE_AND_ATTACK,
        RETURN_TO_KING_THEN_EXPLORE,
    }

    public static Random rand = new Random(1092);

    public static State currentState = State.INITIALIZE;

    public static int numRatsSpawned = 0;
    public static int turnsSinceCarry = 1000;

    public static Direction[] directions = Direction.values();

    public static MapLocation mineLoc = null;
    public static int numMines = 0;
    public static ArrayList<Integer> mineLocs = new ArrayList<>();

    public static boolean exploreWhenFindingCheese = false;
    public static MapLocation targetCheeseMineLoc = null;

    public static void run(RobotController rc) {
        while (true) {
            try {
                if (rc.getType().isRatKingType()) {
                    Economy.runRatKing(rc);
                } else {
                    turnsSinceCarry++;

                    switch (currentState) {
                        case INITIALIZE:
                            if (rc.getRoundNum() < 30 || rc.getCurrentRatCost() <= 10) {
                                currentState = State.FIND_CHEESE;
                                exploreWhenFindingCheese = rand.nextBoolean() && rand.nextBoolean();
                            } else {
                                currentState = State.EXPLORE_AND_ATTACK;
                            }

                            break;
                        case FIND_CHEESE:
                            Economy.runFindCheese(rc);
                            break;
                        case RETURN_TO_KING:
                            Economy.runReturnToKing(rc);
                            break;
                        case BUILD_TRAPS:
                            Defense.runBuildTraps(rc);
                            break;
                        case EXPLORE_AND_ATTACK:
                            Combat.runExploreAndAttack(rc);
                            break;
                        case RETURN_TO_KING_THEN_EXPLORE:
                            Economy.runReturnToKing(rc);

                            if (currentState == State.FIND_CHEESE) {
                                currentState = State.EXPLORE_AND_ATTACK;
                            }
                    }
                }
            } catch (GameActionException e) {
                System.out.println("GameActionException in RobotPlayer:");
                e.printStackTrace();
            } catch (Exception e) {
                System.out.println("Exception in RobotPlayer:");
                e.printStackTrace();
            } finally {
                Clock.yield();
            }
        }
    }
}
