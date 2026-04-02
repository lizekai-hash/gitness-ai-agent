import pygame
import sys
import random

# Initialize pygame
pygame.init()

# Game constants
WINDOW_WIDTH = 600
WINDOW_HEIGHT = 600
GRID_SIZE = 20
GRID_WIDTH = WINDOW_WIDTH // GRID_SIZE
GRID_HEIGHT = WINDOW_HEIGHT // GRID_SIZE
FPS = 12

# Colors
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GREEN = (0, 255, 0)
RED = (255, 0, 0)
DARK_GREEN = (0, 200, 0)
GRAY = (100, 100, 100)

# Directions
UP = (0, -1)
DOWN = (0, 1)
LEFT = (-1, 0)
RIGHT = (1, 0)


class Snake:
    def __init__(self):
        self.reset()
    
    def reset(self):
        """Reset snake to initial state"""
        # Start in the middle of the screen
        start_x = GRID_WIDTH // 2
        start_y = GRID_HEIGHT // 2
        
        # Initial body: 3 segments going right
        self.body = [
            (start_x, start_y),
            (start_x - 1, start_y),
            (start_x - 2, start_y)
        ]
        self.direction = RIGHT
        self.next_direction = RIGHT
        self.grow_pending = False
    
    def update_direction(self, new_direction):
        """Update direction if valid (can't reverse)"""
        # Prevent reversing into itself
        if (new_direction[0] * -1, new_direction[1] * -1) != self.direction:
            self.next_direction = new_direction
    
    def move(self):
        """Move the snake one step in current direction"""
        # Update direction
        self.direction = self.next_direction
        
        # Calculate new head position
        head_x, head_y = self.body[0]
        dx, dy = self.direction
        new_head = ((head_x + dx) % GRID_WIDTH, (head_y + dy) % GRID_HEIGHT)
        
        # Insert new head
        self.body.insert(0, new_head)
        
        # Remove tail if not growing
        if not self.grow_pending:
            self.body.pop()
        else:
            self.grow_pending = False
    
    def grow(self):
        """Mark snake to grow on next move"""
        self.grow_pending = True
    
    def check_collision(self):
        """Check if snake collides with itself"""
        head = self.body[0]
        return head in self.body[1:]
    
    def check_food_collision(self, food_pos):
        """Check if snake head collides with food"""
        return self.body[0] == food_pos
    
    def draw(self, surface):
        """Draw the snake on the surface"""
        for i, (x, y) in enumerate(self.body):
            # Draw each segment
            rect = pygame.Rect(
                x * GRID_SIZE, 
                y * GRID_SIZE, 
                GRID_SIZE, 
                GRID_SIZE
            )
            
            # Head is brighter green
            color = GREEN if i == 0 else DARK_GREEN
            pygame.draw.rect(surface, color, rect)
            pygame.draw.rect(surface, BLACK, rect, 1)  # Border


class Food:
    def __init__(self, snake_body=None):
        self.position = (0, 0)
        self.snake_body = snake_body or []
        self.respawn()
    
    def respawn(self, snake_body=None):
        """Respawn food at random position not occupied by snake"""
        if snake_body:
            self.snake_body = snake_body
        
        while True:
            self.position = (
                random.randint(0, GRID_WIDTH - 1),
                random.randint(0, GRID_HEIGHT - 1)
            )
            # Ensure food doesn't spawn on snake
            if self.position not in self.snake_body:
                break
    
    def draw(self, surface):
        """Draw the food on the surface"""
        x, y = self.position
        rect = pygame.Rect(
            x * GRID_SIZE, 
            y * GRID_SIZE, 
            GRID_SIZE, 
            GRID_SIZE
        )
        pygame.draw.rect(surface, RED, rect)
        pygame.draw.rect(surface, BLACK, rect, 1)  # Border


class Game:
    def __init__(self):
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        pygame.display.set_caption("Snake Game")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont(None, 36)
        self.small_font = pygame.font.SysFont(None, 24)
        
        self.reset()
    
    def reset(self):
        """Reset game to initial state"""
        self.snake = Snake()
        self.food = Food(self.snake.body)
        self.score = 0
        self.game_over = False
        self.running = True
    
    def handle_events(self):
        """Handle pygame events"""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            
            elif event.type == pygame.KEYDOWN:
                if self.game_over:
                    if event.key == pygame.K_r:
                        self.reset()
                else:
                    # Handle direction changes
                    if event.key == pygame.K_UP:
                        self.snake.update_direction(UP)
                    elif event.key == pygame.K_DOWN:
                        self.snake.update_direction(DOWN)
                    elif event.key == pygame.K_LEFT:
                        self.snake.update_direction(LEFT)
                    elif event.key == pygame.K_RIGHT:
                        self.snake.update_direction(RIGHT)
                
                # Always handle R key for restart
                if event.key == pygame.K_r:
                    self.reset()
                
                # Handle quit with ESC
                if event.key == pygame.K_ESCAPE:
                    self.running = False
    
    def update(self):
        """Update game state"""
        if self.game_over:
            return
        
        # Move snake
        self.snake.move()
        
        # Check collisions
        if self.snake.check_collision():
            self.game_over = True
            return
        
        # Check food collision
        if self.snake.check_food_collision(self.food.position):
            self.snake.grow()
            self.food.respawn(self.snake.body)
            self.score += 10
    
    def draw_grid(self):
        """Draw grid lines"""
        for x in range(0, WINDOW_WIDTH, GRID_SIZE):
            pygame.draw.line(self.screen, GRAY, (x, 0), (x, WINDOW_HEIGHT), 1)
        for y in range(0, WINDOW_HEIGHT, GRID_SIZE):
            pygame.draw.line(self.screen, GRAY, (0, y), (WINDOW_WIDTH, y), 1)
    
    def draw_score(self):
        """Draw score display"""
        score_text = self.font.render(f"Score: {self.score}", True, WHITE)
        self.screen.blit(score_text, (10, 10))
    
    def draw_game_over(self):
        """Draw game over screen"""
        # Semi-transparent overlay
        overlay = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        self.screen.blit(overlay, (0, 0))
        
        # Game over text
        game_over_text = self.font.render("GAME OVER", True, RED)
        score_text = self.font.render(f"Final Score: {self.score}", True, WHITE)
        restart_text = self.small_font.render("Press R to Restart", True, GREEN)
        quit_text = self.small_font.render("Press ESC to Quit", True, WHITE)
        
        # Center texts
        self.screen.blit(game_over_text, 
                        (WINDOW_WIDTH // 2 - game_over_text.get_width() // 2, 
                         WINDOW_HEIGHT // 2 - 80))
        self.screen.blit(score_text, 
                        (WINDOW_WIDTH // 2 - score_text.get_width() // 2, 
                         WINDOW_HEIGHT // 2 - 30))
        self.screen.blit(restart_text, 
                        (WINDOW_WIDTH // 2 - restart_text.get_width() // 2, 
                         WINDOW_HEIGHT // 2 + 20))
        self.screen.blit(quit_text, 
                        (WINDOW_WIDTH // 2 - quit_text.get_width() // 2, 
                         WINDOW_HEIGHT // 2 + 50))
    
    def draw_instructions(self):
        """Draw game instructions"""
        instructions = [
            "Use Arrow Keys to Move",
            "Press R to Restart",
            "Press ESC to Quit"
        ]
        
        for i, text in enumerate(instructions):
            instruction = self.small_font.render(text, True, WHITE)
            self.screen.blit(instruction, 
                           (WINDOW_WIDTH - instruction.get_width() - 10, 
                            10 + i * 25))
    
    def draw(self):
        """Draw everything"""
        # Clear screen
        self.screen.fill(BLACK)
        
        # Draw grid
        self.draw_grid()
        
        # Draw game objects
        self.snake.draw(self.screen)
        self.food.draw(self.screen)
        
        # Draw UI
        self.draw_score()
        self.draw_instructions()
        
        # Draw game over screen if needed
        if self.game_over:
            self.draw_game_over()
        
        # Update display
        pygame.display.flip()
    
    def run(self):
        """Main game loop"""
        while self.running:
            self.handle_events()
            self.update()
            self.draw()
            self.clock.tick(FPS)
        
        pygame.quit()
        sys.exit()


if __name__ == "__main__":
    game = Game()
    game.run()
